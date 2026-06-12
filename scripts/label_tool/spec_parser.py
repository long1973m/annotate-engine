"""
Spec 解析器 — 支持 YAML 和 Markdown 格式的标注规范文档

YAML 格式 (推荐):
  schema:
    - field: is_safe
      type: boolean
      description: 是否安全
    - field: risk_tags
      type: array
      description: 风险标签列表
    - field: severity_num
      type: integer
      enum: [0, 1, 2]
      description: 严重程度
  
  system_prompt: "你是一个专业的数据标注员..."
  output_format: json
  
  constraints:
    - "必须对所有风险类别做出判断"
    - "severity_num 不能为空"

Markdown 格式:
  # 标注规范
  
  ## 标注字段
  | 字段 | 类型 | 说明 |
  |---|---|---|
  | is_safe | boolean | 是否安全 |
  
  ## 标注规则
  1. 首先判断是否安全
  2. 如果不安全，标注风险类别...
"""
import re
import yaml
from pathlib import Path
from typing import Any


def load_spec(spec_path: str) -> dict:
    """加载 Spec 文件（自动检测 YAML 或 Markdown）"""
    path = Path(spec_path)
    if not path.exists():
        raise FileNotFoundError(f"Spec 文件不存在: {spec_path}")
    
    ext = path.suffix.lower()
    if ext == ".yaml" or ext == ".yml":
        return _load_yaml_spec(path)
    elif ext == ".md":
        return _load_markdown_spec(path)
    else:
        # 尝试按 YAML 解析
        try:
            return _load_yaml_spec(path)
        except Exception:
            return _load_markdown_spec(path)


def _load_yaml_spec(path: Path) -> dict:
    """解析 YAML 格式的 Spec"""
    with open(path, "r", encoding="utf-8") as f:
        spec = yaml.safe_load(f) or {}
    
    # 验证必要字段
    if "schema" not in spec:
        raise ValueError("YAML Spec 必须包含 'schema' 字段")
    
    return spec


def _load_markdown_spec(path: Path) -> dict:
    """解析 Markdown 格式的 Spec"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    
    spec = {
        "raw": content,
        "schema": [],
        "constraints": [],
    }
    
    # 提取表格（标注字段定义）
    table_pattern = r'\|[\s\-]*\n((?:\|.+\|[\s\-]*\n)+)'
    tables = re.findall(table_pattern, content)
    
    for table_match in tables:
        lines = table_match.strip().split('\n')
        if len(lines) < 2:
            continue
        headers = [h.strip() for h in lines[0].split('|')]
        headers = [h for h in headers if h]
        
        # 跳过分隔行
        data_rows = [l for l in lines[1:] if not all(c in '-| ' for c in l)]
        
        for row in data_rows:
            cells = [c.strip() for c in row.split('|')]
            cells = [c for c in cells if c]
            if len(cells) >= len(headers):
                field = dict(zip(headers, cells[:len(headers)]))
                spec["schema"].append(field)
    
    # 提取编号列表（标注规则/约束）
    constraint_pattern = r'^\d+\.\s+(.+)$'
    for line in content.split('\n'):
        m = re.match(constraint_pattern, line.strip())
        if m:
            spec["constraints"].append(m.group(1))
    
    # 提取系统提示
    sys_prompt_match = re.search(r'##\s*系统提示[^\n]*\n(.+?)(?=^##|$)', content, re.DOTALL | re.MULTILINE)
    if sys_prompt_match:
        spec["system_prompt"] = sys_prompt_match.group(1).strip()
    
    return spec
