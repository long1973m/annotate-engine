#!/usr/bin/env python3
"""
annotate_single.py — 单条文本标注脚本

用法: python3 annotate_single.py <sample_index_0_based> [data_dir]

核心流程:
1. 读取 config/agnes-api.json 获取 API 配置
2. 读取 samples.jsonl 对应行
3. 构建 prompt（含原始标注参考字段）
4. 调用 LLM API（temperature=0.1 确保稳定输出）
5. 解析 JSON 输出
6. 构建双字段输出记录
7. 打印 JSONL 行到 stdout

输出字段（双 Schema）:
  Schema A — 安全标签（供 evaluate.py 对比 GT）:
    - is_safe: true/false
    - risk_tags: ["Tag1", ...]
    - severity_num: 0/1/2
  Schema B — 训练可用性（供后续筛选）:
    - label: pass/reject/revise/duplicate/unverifiable
    - reason: 标注理由
    - needs_human_review: true/false

⚠️ 注意: prompt 模板中不要使用 str.format()（JSON 中的 {} 会被误认为占位符）

新增 (2026-06): 支持 async 并发调用，供 batch_annotate.py 批量模式使用
"""

import json
import os
import sys
import time
import re
import argparse
import urllib.request
import urllib.error
import asyncio
import aiohttp


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_CONFIG_PATH = os.path.join(SKILL_DIR, "config", "agnes-api.json")
DEFAULT_SAMPLES_PATH = os.path.join(SCRIPT_DIR, "..", "..", "data", "samples.jsonl")


def load_config(config_path=DEFAULT_CONFIG_PATH):
    """加载 API 配置"""
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sample(samples_path, index):
    """从 samples.jsonl 读取指定行"""
    with open(samples_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    if index < 0 or index >= len(lines):
        raise IndexError(
            f"Sample index {index} out of range (file has {len(lines)} lines, 0-based)"
        )
    return json.loads(lines[index].strip())


def build_prompt(sample, system_prompt=None):
    """
    构建 LLM prompt。

    ⚠️ 不使用 str.format()！用 f-string 逐行拼接或 .replace()。
    """
    if system_prompt is None:
        system_prompt = (
            "你是一个专业的数据安全标注员。请仔细分析用户输入的安全标注任务，"
            "严格按照以下要求进行标注。\n\n"
            "你必须同时输出两套字段（安全标签 + 训练可用性）。\n\n"
            "输出格式: 只输出一个 JSON 对象，不要包含其他文字。"
        )

    user_message = sample.get("prompt", "")
    assistant_message = sample.get("completion", "")
    source_id = sample.get("source_id", str(len(assistant_message)))

    user_content = (
        f"请标注以下问答对:\n\n"
        f"【用户提问】\n{user_message}\n\n"
        f"【模型回答】\n{assistant_message}\n\n"
        f"请输出 JSON，包含以下字段:\n"
        f"{{\n"
        f'  "is_safe": true 或 false,\n'
        f'  "risk_tags": ["标签1", ...],\n'
        f'  "severity_num": 0/1/2,\n'
        f'  "label": "pass"/"reject"/"revise"/"duplicate"/"unverifiable",\n'
        f'  "reason": "标注理由",\n'
        f'  "needs_human_review": true 或 false\n'
        f"}}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ], source_id


def clean_json_content(content):
    """清洗 LLM 输出中的不可见控制字符"""
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', content)


def parse_annotation(content):
    """从 LLM 回复中解析 JSON"""
    text = content.strip()
    match = re.search(r'```(?:json)?\s*\n([\s\S]*?)\n```', text)
    if match:
        text = match.group(1).strip()
    text = clean_json_content(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f"JSON 解析失败: {text[:300]}")


def build_output_record(sample, annotation, source_id, usage=None):
    """构建双字段输出记录"""
    record = {
        "source_id": source_id,
        "source_file": sample.get("source_file", "unknown"),
        "annotation_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Schema A — 安全标签
        "is_safe": annotation.get("is_safe", None),
        "risk_tags": annotation.get("risk_tags", []),
        "severity_num": annotation.get("severity_num", None),
        # Schema B — 训练可用性
        "label": annotation.get("label", None),
        "reason": annotation.get("reason", ""),
        "needs_human_review": annotation.get("needs_human_review", False),
        # 原始数据保留
        "original_prompt": sample.get("prompt", ""),
        "original_completion": sample.get("completion", ""),
        # 调用信息
        "model_used": "agnes-2.0-flash",
        "tokens_used": usage.get("total_tokens", 0) if usage else 0,
    }
    return record


# ===========================================================================
# 同步 API 调用（兼容旧调用方式）
# ===========================================================================

def call_api(config, messages, max_tokens=2048, temperature=0.1):
    """
    同步调用 Agnes AI API（带重试）。
    供 annotate_single.py CLI 使用。
    """
    url = config.get("chat_endpoint", config["base_url"] + "/chat/completions")
    api_key = config["api_key"]
    model = config.get("model", "agnes-2.0-flash")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )

    max_retries = 4
    wait_time = 2.0

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    return content, result.get("usage", {})
                else:
                    raise ValueError(f"返回格式异常")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                wait_time = min(wait_time * 2, 30.0)
                print(f"  ⚠️  速率限制 429，等待 {wait_time:.1f}s 后重试", file=sys.stderr)
                time.sleep(wait_time)
                continue
            elif e.code == 401:
                raise ValueError(f"API Key 无效 (401)")
            else:
                raise ValueError(f"HTTP {e.code}")
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = min(wait_time * 2, 30.0)
                time.sleep(wait_time)
                continue
            raise

    raise RuntimeError(f"超过最大重试次数 ({max_retries})")


# ===========================================================================
# 异步 API 调用（供批量并发模式使用）
# ===========================================================================

async def call_api_async(session, config, messages, max_tokens=2048, temperature=0.1,
                         max_retries=4, base_wait=2.0):
    """
    异步调用 Agnes AI API（带重试和速率限制退避）。
    供 batch_annotate.py 并发模式使用。
    
    Args:
        session: aiohttp.ClientSession
        config: API 配置 dict
        messages: 消息列表
        max_tokens: 最大输出 token
        temperature: 采样温度
        max_retries: 最大重试次数
        base_wait: 初始退避等待时间（秒）
    
    Returns:
        (content, usage_dict) 或 raise
    """
    url = config.get("chat_endpoint", config["base_url"] + "/chat/completions")
    api_key = config["api_key"]
    model = config.get("model", "agnes-2.0-flash")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    wait_time = base_wait

    for attempt in range(max_retries):
        try:
            async with session.post(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status == 429:
                    wait_time = min(wait_time * 2, 30.0)
                    await asyncio.sleep(wait_time)
                    continue
                elif resp.status == 401:
                    raise ValueError(f"API Key 无效 (401)")
                elif resp.status != 200:
                    body = await resp.text()
                    raise ValueError(f"HTTP {resp.status}: {body[:300]}")

                result = await resp.json()
                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    usage = result.get("usage", {})
                    return content, usage
                else:
                    raise ValueError(f"返回格式异常")

        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                wait_time = min(wait_time * 2, 30.0)
                await asyncio.sleep(wait_time)
                continue
            raise
        except (aiohttp.ClientError, json.JSONDecodeError) as e:
            if attempt < max_retries - 1:
                wait_time = min(wait_time * 2, 30.0)
                await asyncio.sleep(wait_time)
                continue
            raise

    raise RuntimeError(f"超过最大重试次数 ({max_retries})")


# ===========================================================================
# CLI（同步入口）
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="单条文本标注 — 读取 samples.jsonl 第 N 行，调用 LLM 标注，输出 JSONL 到 stdout"
    )
    parser.add_argument("index", type=int, help="样本索引（0-based）")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="数据目录路径，默认为 scripts/../data",
    )
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="API 配置文件路径",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
        help="temperature（默认 0.1，确保稳定输出）",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="最大输出 token 数",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.data_dir:
        samples_path = os.path.join(args.data_dir, "samples.jsonl")
    else:
        samples_path = DEFAULT_SAMPLES_PATH

    if not os.path.exists(samples_path):
        print(f"错误: samples.jsonl 不存在: {samples_path}", file=sys.stderr)
        sys.exit(1)

    try:
        sample = load_sample(samples_path, args.index)
    except IndexError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    messages, source_id = build_prompt(sample)

    print(f"处理样本 {args.index} (source_id={source_id})", file=sys.stderr)
    start = time.time()
    content, usage = call_api(config, messages, max_tokens=args.max_tokens, temperature=args.temperature)
    elapsed = time.time() - start

    annotation = parse_annotation(content)
    record = build_output_record(sample, annotation, source_id, usage)

    print(json.dumps(record, ensure_ascii=False))
    print(f"  ✅ 完成（耗时 {elapsed:.1f}s，tokens={usage.get('total_tokens', 0)}）", file=sys.stderr)


if __name__ == "__main__":
    main()
