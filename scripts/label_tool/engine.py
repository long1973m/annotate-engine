"""
标注引擎 — 核心编排：并发、断点续传、速率限制、进度追踪

流程:
  1. 加载 Spec → 构建 prompt 模板
  2. 加载数据 → 生成待标注列表
  3. 检查输出文件 → 跳过已标注记录（断点续传）
  4. 批量并发调用 API
  5. 解析结果 → 更新记录
  6. 写入输出文件
"""
import json
import time
import asyncio
from pathlib import Path
from typing import Optional

from .input_loader import Record
from .api_client import APIClient
from .output_writer import write_output


def build_prompt_template(records: list, spec: dict) -> list:
    """
    为每条记录构建 prompt
    
    支持两种 Spec 格式：
    - YAML: schema 定义了字段和 JSON 输出要求
    - Markdown: raw 字段为原始文本，需要内部模板
    
    Returns:
        [{"prompt": "...", "system_prompt": "...", "response_format": {...}}]
    """
    system_prompt = spec.get("system_prompt", "你是一个专业的数据标注员，请按照规范进行标注。")
    
    # 构建 JSON schema（从 spec 的 schema 字段）
    json_schema = _build_json_schema(spec)
    
    requests = []
    for rec in records:
        # 构建数据文本
        data_text = _format_record(rec, spec)
        
        # 构建 prompt
        if json_schema:
            prompt = f"""{spec.get("instructions", "请按照以下规范进行标注：")}

待标注数据：
{data_text}

请严格按照以下 JSON 格式输出：
{json_schema}

只输出 JSON，不要输出其他内容。"""
        else:
            prompt = f"""{system_prompt}

待标注数据：
{data_text}

请按要求完成标注。"""
        
        req = {
            "prompt": prompt,
            "system_prompt": system_prompt,
        }
        if json_schema:
            req["response_format"] = {"type": "json_object"}
        requests.append(req)
    
    return requests


def _build_json_schema(spec: dict) -> Optional[str]:
    """从 spec 的 schema 字段构建 JSON Schema 描述"""
    schema_fields = spec.get("schema", [])
    if not schema_fields:
        return None
    
    # 构建 JSON Schema
    props = {}
    required = []
    
    for field in schema_fields:
        name = field.get("field", field.get("name", ""))
        if not name:
            continue
        
        prop = {
            "description": field.get("description", ""),
        }
        
        ftype = field.get("type", "string")
        type_map = {
            "boolean": "boolean",
            "bool": "boolean",
            "integer": "integer",
            "int": "integer",
            "number": "number",
            "string": "string",
            "array": "array",
            "list": "array",
        }
        prop["type"] = type_map.get(ftype, "string")
        
        if "enum" in field:
            prop["enum"] = field["enum"]
        
        if prop["type"] == "array" and "items" in field:
            prop["items"] = {"type": field["items"].get("type", "string")}
        
        props[name] = prop
        required.append(name)
    
    full_schema = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }
    
    return json.dumps(full_schema, ensure_ascii=False, indent=2)


def _format_record(rec: Record, spec: dict) -> str:
    """将 Record 格式化为文本"""
    raw = rec.raw
    lines = []
    for key, value in raw.items():
        if value and str(value).strip():
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> Optional[str]:
    """用 brace 计数提取完整的 JSON 对象，正确处理字符串内的括号"""
    depth = 0
    start = None
    in_string = False
    escape = False

    for i, c in enumerate(text):
        if escape:
            escape = False
            continue
        if c == '\\' and in_string:
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue

        if c == '{':
            if start is None:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                return text[start:i + 1]
    return None


def parse_annotation(content: str, spec: dict) -> Optional[dict]:
    """
    解析 LLM 返回的标注结果
    
    处理常见的 JSON 解析问题：
    - 去除 markdown 代码块标记
    - 提取第一个合法 JSON
    - 处理 trailing comma 等常见问题
    """
    # 去除 markdown 代码块（支持内容前后有文字的情况）
    content = content.strip()
    if "```" in content:
        lines = content.split('\n')
        json_start = None
        json_end = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("```"):
                if json_start is None:
                    json_start = i + 1
                elif json_end is None:
                    json_end = i
                    break
        if json_start is not None and json_end is not None:
            content = '\n'.join(lines[json_start:json_end])
    
    # 尝试直接解析
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    
    # 尝试提取 JSON 对象（brace 计数，处理嵌套和字符串内的括号）
    json_str = _extract_json_object(content)
    if json_str:
        # 修复 trailing comma
        json_str = json_str.rstrip().rstrip(',').rstrip() + '}'
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    
    # 解析失败
    return {"_parse_error": True, "_raw": content[:500]}


async def annotate_batch(records: list, api_client: APIClient, 
                         spec: dict, workers: int = 5,
                         batch_size: int = 50,
                         progress_callback=None) -> list:
    """
    批量标注
    
    Args:
        records: Record 列表
        api_client: API 客户端
        spec: 标注规范
        workers: 并发数
        batch_size: 每批处理的记录数（用于限速）
        progress_callback: 进度回调 callback(done, total)
    
    Returns:
        Record 列表（已更新 annotation 和 status）
    """
    requests = build_prompt_template(records, spec)
    total = len(records)
    done = 0
    
    # 断点续传：跳过已标注的记录
    pending_indices = []
    for i, rec in enumerate(records):
        if rec.status == "done" and rec.annotation is not None:
            done += 1
        else:
            pending_indices.append(i)
    
    pending_count = len(pending_indices)
    if pending_count == 0:
        print("所有记录已标注完成，无需继续。")
        return records
    
    print(f"待标注: {pending_count}/{total} 条")
    
    # 分批处理
    for batch_start in range(0, pending_count, batch_size):
        batch_end = min(batch_start + batch_size, pending_count)
        batch_indices = pending_indices[batch_start:batch_end]
        
        batch_requests = [requests[i] for i in batch_indices]
        
        # 并发调用
        results = await api_client.call_batch(batch_requests, workers=workers)
        
        # 解析结果并更新记录
        for j, (idx, content) in enumerate(zip(batch_indices, results)):
            rec = records[idx]
            if content is None:
                rec.status = "error"
                rec.annotation = {"_error": "API 返回为空"}
            elif content.startswith("ERROR:"):
                rec.status = "error"
                rec.annotation = {"_error": content[6:]}
            else:
                annotation = parse_annotation(content, spec)
                rec.annotation = annotation or {"_parse_error": True, "_raw": content[:200]}
                rec.status = "done"
            
            done += 1
            if progress_callback:
                progress_callback(done, total)
    
    return records


def run_sync(records: list, api_client: APIClient, spec: dict,
             workers: int = 5, batch_size: int = 50,
             progress_callback=None) -> list:
    """同步入口（内部用 asyncio.run）"""
    return asyncio.run(annotate_batch(
        records, api_client, spec,
        workers=workers, batch_size=batch_size,
        progress_callback=progress_callback,
    ))
