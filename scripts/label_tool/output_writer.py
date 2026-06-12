"""
输出写入器 — 支持 CSV 和 JSONL 双格式

支持断点续传：已标注的记录追加，未标注的跳过
"""
import csv
import json
from pathlib import Path
from typing import Optional
from .input_loader import Record


def write_jsonl(records: list, output_path: str, append: bool = True):
    """
    写入 JSONL 格式
    
    Args:
        records: Record 列表
        output_path: 输出路径
        append: True=追加模式（跳过已存在的 source_id），False=覆盖
    """
    existing_ids = set()
    if append and Path(output_path).exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        sid = obj.get("_meta", {}).get("source_id")
                        if sid:
                            existing_ids.add(sid)
                    except json.JSONDecodeError:
                        continue
    
    with open(output_path, "a", encoding="utf-8") as f:
        for rec in records:
            if rec.status == "done" and rec.annotation is not None:
                # 跳过已存在的
                if existing_ids and rec.source_id in existing_ids:
                    continue
                obj = _record_to_json(rec)
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                if rec.source_id in existing_ids:
                    existing_ids.discard(rec.source_id)


def write_csv(records: list, output_path: str, append: bool = True):
    """
    写入 CSV 格式
    
    表头: 原始字段 + _annotation_ 前缀的标注字段
    """
    if not records:
        return
    
    # 收集所有字段
    all_fields = set(records[0].raw.keys())
    all_fields.add("_annotation_")
    all_fields.add("_source_id")
    all_fields.add("_status")
    
    existing_ids = set()
    if append and Path(output_path).exists():
        with open(output_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sid = row.get("_source_id")
                if sid:
                    existing_ids.add(sid)
    
    with open(output_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = None
        for rec in records:
            if rec.status != "done" or rec.annotation is None:
                continue
            if existing_ids and rec.source_id in existing_ids:
                continue
            
            row = dict(rec.raw)
            row["_annotation_"] = json.dumps(rec.annotation, ensure_ascii=False)
            row["_source_id"] = rec.source_id
            row["_status"] = rec.status
            
            if writer is None:
                writer = csv.DictWriter(f, fieldnames=sorted(row.keys()))
                if not existing_ids:
                    writer.writeheader()
            writer.writerow(row)


def write_output(records: list, output_path: str, append: bool = True):
    """自动检测格式并写入"""
    ext = Path(output_path).suffix.lower()
    if ext == ".csv":
        write_csv(records, output_path, append)
    elif ext in (".jsonl", ".json"):
        write_jsonl(records, output_path, append)
    else:
        raise ValueError(f"不支持的输出格式: {ext}（仅支持 .csv 和 .jsonl）")


def _record_to_json(rec: Record) -> dict:
    """Record 转为 JSON 对象"""
    result = {
        "_meta": {
            "id": rec.id,
            "source_id": rec.source_id,
            "status": rec.status,
        },
        **rec.raw,
    }
    if rec.annotation:
        result["_annotation_"] = rec.annotation
    return result
