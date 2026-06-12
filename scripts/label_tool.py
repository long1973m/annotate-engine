#!/usr/bin/env python3
"""
label_tool — 通用 AI 数据标注 CLI 工具

用法:
  python3 label_tool.py --config config.yaml --spec spec.yaml --input data.csv --output results.jsonl --workers 5
  python3 label_tool.py --config config.yaml --spec spec.yaml --input data.jsonl --output results.csv --resume --workers 10
"""
import sys
import os

# 确保 label_tool 包可被导入（脚本在 scripts/ 目录下，label_tool 包在同一级）
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from label_tool.cli import main

if __name__ == "__main__":
    sys.exit(main())
