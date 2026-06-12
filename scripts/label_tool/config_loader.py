"""
配置加载器 — 支持多模型、环境变量替换、密钥管理

配置格式 (YAML):
  models:
    default: agnes-2.0-flash
    providers:
      agnes:
        base_url: https://apihub.agnes-ai.com/v1
        api_key: ${AGNES_API_KEY}          # 环境变量替换
        models:
          - name: agnes-2.0-flash
            max_tokens: 4096
            temperature: 0.7
      openai:
        base_url: https://api.openai.com/v1
        api_key: ${OPENAI_API_KEY}
        models:
          - name: gpt-4o
            max_tokens: 8192
            temperature: 0.7

用法:
  config = load_config("config.yaml")
  provider_cfg = config["providers"]["agnes"]
  model_name = config.get("models", {}).get("default", "agnes-2.0-flash")
"""
import os
import re
import yaml
from pathlib import Path
from typing import Any


def _expand_env_vars(value: str) -> str:
    """替换 ${VAR} 和 $VAR 格式的环境变量"""
    if not isinstance(value, str):
        return value
    pattern = r'\$\{(\w+)\}|\$(\w+)'
    def replacer(match):
        var = match.group(1) or match.group(2)
        env_val = os.environ.get(var)
        if env_val is not None:
            return env_val
        return match.group(0)  # 找不到就保留原样
    return re.sub(pattern, replacer, value)


def _deep_expand(obj: Any) -> Any:
    """递归展开所有字符串中的环境变量"""
    if isinstance(obj, str):
        return _expand_env_vars(obj)
    elif isinstance(obj, dict):
        return {k: _deep_expand(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_expand(v) for v in obj]
    return obj


def load_config(config_path: str) -> dict:
    """加载并解析配置文件"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    
    config = _deep_expand(raw)
    
    # 验证基本结构
    if "providers" not in config:
        raise ValueError("配置必须包含 'providers' 字段")
    
    for name, provider in config["providers"].items():
        if "base_url" not in provider:
            raise ValueError(f"Provider '{name}' 缺少 base_url")
        if "api_key" not in provider:
            raise ValueError(f"Provider '{name}' 缺少 api_key")
        if "models" not in provider or not provider["models"]:
            raise ValueError(f"Provider '{name}' 缺少 models 列表")
    
    # 确保 default 指向一个存在的模型
    default_name = config.get("models", {}).get("default")
    if default_name:
        found = False
        for provider in config["providers"].values():
            for model in provider.get("models", []):
                if model.get("name") == default_name:
                    found = True
                    break
            if found:
                break
        if not found:
            raise ValueError(f"默认模型 '{default_name}' 未在 providers 中定义")
    
    return config


def get_model_info(config: dict, model_name: str = None) -> tuple:
    """
    根据模型名获取 provider 和 model 配置
    
    Returns:
        (provider_name, provider_cfg, model_cfg)
    """
    target = model_name or config.get("models", {}).get("default")
    if not target:
        raise ValueError("未指定默认模型，需显式传入 model_name")
    
    for provider_name, provider_cfg in config["providers"].items():
        for model in provider_cfg.get("models", []):
            if model.get("name") == target:
                return provider_name, provider_cfg, model
    
    raise ValueError(f"模型 '{target}' 未在任何 provider 中定义")
