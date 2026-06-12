"""
输入加载器 — 支持 CSV 和 JSONL 格式

统一输出为 list[Record]，每条 Record:
  {
    "id": 整数索引,
    "raw": dict,          # 原始字段
    "source_id": str,     # 用于断点续传的唯一标识
    "status": "pending",  # pending / done / error
  }
"""
import csv
import json
from pathlib import Path
from typing import Optional


class Record:
    """标注数据记录"""
    def __init__(self, id: int, raw: dict, source_id: str = None):
        self.id = id
        self.raw = raw
        self.source_id = source_id or str(id)
        self.status = "pending"
        self.annotation = None
    
    def __repr__(self):
        return f"Record(id={self.id}, status={self.status}, source_id={self.source_id})"


def load_csv(input_path: str, id_field: str = None) -> list:
    """
    加载 CSV 文件
    
    Args:
        input_path: CSV 文件路径
        id_field: 可选，用作 source_id 的字段名。未指定时用行号索引
    
    Returns:
        list[Record]
    """
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    
    records = []
    # UTF-8 BOM 处理
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            raw = dict(row)
            source_id = raw.get(id_field) if id_field else str(i)
            records.append(Record(id=i, raw=raw, source_id=source_id))
    
    return records


def load_jsonl(input_path: str, id_field: str = None) -> list:
    """
    加载 JSONL 文件
    
    Args:
        input_path: JSONL 文件路径
        id_field: 可选，用作 source_id 的字段名
    
    Returns:
        list[Record]
    """
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            source_id = raw.get(id_field) if id_field else str(i)
            records.append(Record(id=i, raw=raw, source_id=source_id))
    
    return records


def load_input(input_path: str, id_field: str = None) -> list:
    """
    自动检测格式并加载
    
    支持: .csv, .jsonl
    """
    ext = Path(input_path).suffix.lower()
    if ext == ".csv":
        return load_csv(input_path, id_field)
    elif ext in (".jsonl", ".json"):
        return load_jsonl(input_path, id_field)
    else:
        raise ValueError(f"不支持的文件格式: {ext}（仅支持 .csv 和 .jsonl）")
