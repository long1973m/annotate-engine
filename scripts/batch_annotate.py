#!/usr/bin/env python3
"""
batch_annotate.py — 批量文本标注脚本（断点续传 + 并发）

用法:
  python3 batch_annotate.py <start_index> <batch_size> [data_dir]
  python3 batch_annotate.py 0 100 /path/to/project/data

核心特性:
- 断点续传：自动跳过 dataset.jsonl 中已存在的 source_id
- 进度追踪：更新 plan.json 中每条的状态
- 容错：单条失败不中断整个批次
- 增量写入：追加到 dataset.jsonl，不重写全量
- 并发控制：asyncio 并发调用 API（默认并发度 5）
- 速率限制：批次间隔 + 429 指数退避

⚠️ prompt 模板中不要使用 str.format()（JSON 中的 {} 会被误认为占位符）
"""

import json
import os
import sys
import time
import re
import argparse
import asyncio
import aiohttp

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from annotate_single import (
    load_config,
    load_sample,
    build_prompt,
    parse_annotation,
    build_output_record,
    call_api_async,
)

DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "..", "config", "agnes-api.json")


# ---------------------------------------------------------------------------
# 文件操作
# ---------------------------------------------------------------------------

def ensure_results_dir(data_dir):
    results_dir = os.path.join(data_dir, "results")
    os.makedirs(results_dir, exist_ok=True)
    return results_dir


def load_plan(results_dir):
    plan_path = os.path.join(results_dir, "plan.json")
    if os.path.exists(plan_path):
        with open(plan_path, "r", encoding="utf-8") as f:
            return json.load(f), plan_path
    return None, plan_path


def save_plan(plan, plan_path):
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)


def load_existing_source_ids(results_dir):
    """加载已标注记录的 source_id 集合（用于断点续传）"""
    dataset_path = os.path.join(results_dir, "dataset.jsonl")
    existing = set()
    if os.path.exists(dataset_path):
        with open(dataset_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        record = json.loads(line)
                        sid = record.get("source_id")
                        if sid:
                            existing.add(sid)
                    except json.JSONDecodeError:
                        continue
    return existing


def update_plan_item(plan, item_id, status, result_file=None, elapsed=None):
    for item in plan.get("items", []):
        if item.get("id") == item_id:
            item["status"] = status
            if result_file:
                item["result_file"] = result_file
            if elapsed is not None:
                item["elapsed"] = round(elapsed, 2)
            break
    plan["processed"] = sum(
        1 for i in plan.get("items", [])
        if i.get("status") in ("done", "failed")
    )
    plan["failed"] = sum(
        1 for i in plan.get("items", [])
        if i.get("status") == "failed"
    )


def append_dataset(results_dir, record):
    dataset_path = os.path.join(results_dir, "dataset.jsonl")
    with open(dataset_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 并发标注核心
# ---------------------------------------------------------------------------

async def annotate_one(item_index, plan, config, data_dir, results_dir,
                       temperature, max_tokens, lock, stats):
    """
    异步标注单条数据。

    Args:
        item_index: plan.json 中的索引
        plan: 计划 dict
        config: API 配置
        data_dir: 数据目录
        results_dir: 结果目录
        lock: asyncio.Lock（保护共享状态）
        stats: 统计 dict（带锁访问）
    """
    plan_item = plan["items"][item_index]
    item_id = plan_item["id"]
    sample_idx = item_index

    update_plan_item(plan, item_id, "processing")
    save_plan(plan, os.path.join(results_dir, "plan.json"))

    try:
        sample = load_sample(os.path.join(data_dir, "samples.jsonl"), sample_idx)
    except Exception as e:
        async with lock:
            stats["failed"] += 1
            stats["total_fail"] += 1
            update_plan_item(plan, item_id, "failed", elapsed=0)
            save_plan(plan, os.path.join(results_dir, "plan.json"))
        return

    item_start = time.time()

    try:
        messages, source_id = build_prompt(sample)

        # 使用 aiohttp 并发调用
        connector = aiohttp.TCPConnector(limit=10, force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            content, usage = await call_api_async(
                session, config, messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        annotation = parse_annotation(content)
        record = build_output_record(sample, annotation, source_id, usage)
        append_dataset(results_dir, record)
        elapsed = time.time() - item_start

        async with lock:
            stats["success"] += 1
            stats["total_success"] += 1
            update_plan_item(plan, item_id, "done", elapsed=elapsed)
            save_plan(plan, os.path.join(results_dir, "plan.json"))

        print(f"  [✅] 样本 {sample_idx}: done "
              f"(成功={stats['total_success']}, 失败={stats['total_fail']}, 耗时{elapsed:.1f}s)")

    except Exception as e:
        elapsed = time.time() - item_start
        async with lock:
            stats["failed"] += 1
            stats["total_fail"] += 1
            update_plan_item(plan, item_id, "failed", elapsed=elapsed)
            save_plan(plan, os.path.join(results_dir, "plan.json"))
        print(f"  [❌] 样本 {sample_idx}: failed ({stats['total_fail']} 失败, 耗时{elapsed:.1f}s) 错误: {e}")


async def run_batch(indices, plan, config, data_dir, results_dir,
                    concurrency, batch_interval, temperature, max_tokens):
    """
    并发执行批量标注。

    Args:
        indices: 待处理样本的索引列表
        plan: 计划 dict
        config: API 配置
        data_dir: 数据目录
        results_dir: 结果目录
        concurrency: 并发度（同时调用的请求数）
        batch_interval: 每批之间的间隔（秒），用于避免限速
        temperature: 采样温度
        max_tokens: 最大输出 token
    """
    lock = asyncio.Lock()
    stats = {"success": 0, "failed": 0, "total_success": 0, "total_fail": 0}

    batch_start = time.time()
    total = len(indices)

    # 分批次处理，每批最多 concurrency 条
    batch_num = 0
    for i in range(0, total, concurrency):
        batch_num += 1
        batch_indices = indices[i:i + concurrency]
        batch_start_time = time.time()

        print(f"\n📊 第 {batch_num} 批：{len(batch_indices)} 条（累计待处理 {total - i} 条）", file=sys.stderr)

        # 并发执行本批
        tasks = []
        for idx in batch_indices:
            task = asyncio.create_task(
                annotate_one(idx, plan, config, data_dir, results_dir,
                            temperature, max_tokens, lock, stats)
            )
            tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

        # 批次间等待（避免触发限速）
        if batch_interval > 0 and (i + concurrency) < total:
            batch_elapsed = time.time() - batch_start_time
            sleep_time = max(0, batch_interval - batch_elapsed)
            if sleep_time > 0:
                print(f"  ⏳ 批次间隔 {sleep_time:.1f}s（已用{batch_elapsed:.1f}s）", file=sys.stderr)
                await asyncio.sleep(sleep_time)

    batch_elapsed = time.time() - batch_start
    print(f"\n{'='*50}", file=sys.stderr)
    print(f"📊 批量标注完成！", file=sys.stderr)
    print(f"  本次处理: {total} 条", file=sys.stderr)
    print(f"  累计成功: {stats['total_success']}", file=sys.stderr)
    print(f"  累计失败: {stats['total_fail']}", file=sys.stderr)
    print(f"  总耗时: {batch_elapsed:.1f}s（{total/batch_elapsed:.1f} 条/秒）", file=sys.stderr)
    print(f"  结果文件: {os.path.join(results_dir, 'dataset.jsonl')}", file=sys.stderr)
    print(f"{'='*50}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="批量文本标注 — 支持断点续传、并发调用、自动限速"
    )
    parser.add_argument("start", type=int, help="起始索引（0-based）")
    parser.add_argument("batch_size", type=int, help="本次处理的样本数量")
    parser.add_argument(
        "data_dir",
        nargs="?",
        default=None,
        help="数据目录路径",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="API 配置文件路径")
    parser.add_argument("--temperature", type=float, default=0.1, help="temperature（默认 0.1）")
    parser.add_argument("--max-tokens", type=int, default=2048, help="最大输出 token 数")
    parser.add_argument(
        "--concurrency", type=int, default=5,
        help="并发度（同时调用的 API 请求数，默认 5，建议 3-8）",
    )
    parser.add_argument(
        "--interval", type=float, default=3.0,
        help="每批之间的间隔秒数（默认 3s，用于避免触发速率限制）",
    )
    parser.add_argument(
        "--sync", action="store_true",
        help="使用同步模式（逐条，不并发）",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.data_dir:
        data_dir = args.data_dir
    else:
        data_dir = os.path.join(SCRIPT_DIR, "..", "..", "data")

    if not os.path.isdir(data_dir):
        print(f"错误: 数据目录不存在: {data_dir}", file=sys.stderr)
        sys.exit(1)

    results_dir = ensure_results_dir(data_dir)

    # 加载 plan
    plan, plan_path = load_plan(results_dir)
    if plan is None:
        samples_path = os.path.join(data_dir, "samples.jsonl")
        if not os.path.exists(samples_path):
            print(f"错误: 找不到 samples.jsonl，请先创建 plan.json", file=sys.stderr)
            sys.exit(1)

        with open(samples_path, "r", encoding="utf-8") as f:
            sample_count = sum(1 for line in f if line.strip())

        plan = {
            "task_name": "批量文本标注",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_items": sample_count,
            "processed": 0,
            "failed": 0,
            "items": [
                {"id": i + 1, "source": f"sample_{i}", "type": "text", "status": "pending", "result_file": None}
                for i in range(sample_count)
            ],
        }
        save_plan(plan, plan_path)
        print(f"已自动生成 plan.json（{sample_count} 条样本）", file=sys.stderr)

    # 获取已有 source_id（断点续传）
    existing_sids = load_existing_source_ids(results_dir) if not args.sync else set()
    if existing_sids:
        print(f"发现 {len(existing_sids)} 条已有记录，将跳过", file=sys.stderr)

    # 确定待处理范围
    start_idx = args.start
    end_idx = start_idx + args.batch_size

    pending_indices = []
    skipped = 0

    for i in range(start_idx, end_idx):
        plan_item = plan["items"][i] if i < len(plan["items"]) else None
        if plan_item is None:
            break

        # 匹配 source_id
        matched_sid = None
        # 1. 用 plan item 的 source
        sid = plan_item.get("source", plan_item.get("id", f"sample_{i}"))
        if sid in existing_sids and plan_item["status"] == "done":
            skipped += 1
            continue

        # 2. 尝试从样本获取 source_id
        try:
            sample = load_sample(os.path.join(data_dir, "samples.jsonl"), i)
            sample_sid = sample.get("source_id", f"sample_{i}")
            if sample_sid in existing_sids:
                skipped += 1
                continue
        except Exception:
            pass

        pending_indices.append(i)

    if not pending_indices:
        print(f"没有待处理的样本（已跳过 {skipped} 条已有记录）", file=sys.stderr)
        return

    print(f"待处理: {len(pending_indices)} 条（跳过 {skipped} 条）", file=sys.stderr)

    if args.sync:
        # 同步模式（逐条）
        print(f"使用同步模式（并发度=1）", file=sys.stderr)
        asyncio.run(run_batch(pending_indices, plan, config, data_dir, results_dir,
                             concurrency=1, batch_interval=args.interval,
                             temperature=args.temperature, max_tokens=args.max_tokens))
    else:
        # 异步并发模式
        print(f"使用并发模式（并发度={args.concurrency}，批次间隔={args.interval}s）", file=sys.stderr)
        asyncio.run(run_batch(pending_indices, plan, config, data_dir, results_dir,
                             concurrency=args.concurrency, batch_interval=args.interval,
                             temperature=args.temperature, max_tokens=args.max_tokens))


if __name__ == "__main__":
    main()
