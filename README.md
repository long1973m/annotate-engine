# annotate — 标注引擎 CLI

独立的数据标注工具，支持多种 LLM provider，通过 YAML 规范驱动标注任务。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key
cp config.example.yaml config.yaml
# 编辑 config.yaml 填入你的 key，或直接用环境变量：
export AGNES_API_KEY="你的key"

# 3. 运行标注
./annotate run --config config.yaml --spec spec.yaml --input data.csv
```

## 子命令

| 命令 | 用途 | 示例 |
|------|------|------|
| `run` | 执行标注 | `./annotate run --config config.yaml --spec spec.yaml --input data.csv` |
| `stats` | 查看统计 | `./annotate stats results.jsonl` |
| `view` | 生成查看器 | `./annotate view ./my_project` |
| `validate` | 格式校验 | `./annotate validate results.jsonl --spec spec.yaml` |

## 配置文件

```yaml
# config.yaml
models:
  default: agnes-2.0-flash

providers:
  agnes:
    base_url: https://apihub.agnes-ai.com/v1
    api_key: ${AGNES_API_KEY}  # 自动读取环境变量
    models:
      - name: agnes-2.0-flash
        max_tokens: 4096
        temperature: 0.7
```

支持多 provider（agnes / openai / ollama 等），`api_key` 支持 `${ENV_VAR}` 语法。

## 标注规范 (Spec)

YAML 格式：

```yaml
system_prompt: "你是一个专业的数据标注员"
instructions: "请根据以下标准进行标注"
schema:
  - field: is_safe
    type: boolean
    description: "内容是否安全"
  - field: risk_tags
    type: array
    items:
      type: string
    description: "风险标签列表"
```

Markdown 格式也支持（自动解析表格为 schema）。

## run 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config` | 配置文件路径 | 必填 |
| `--spec` | 标注规范路径 | 必填 |
| `--input` | 输入数据 (.csv/.jsonl) | 必填 |
| `--output` | 输出文件路径 | `{input}_annotated.jsonl` |
| `--model` | 覆盖默认模型 | 配置中的 default |
| `--workers` | 并发数 | 5 |
| `--resume` | 断点续标 | false |
| `--batch-size` | 批次大小 | 50 |

## 目录结构

```
annotate-engine/
├── annotate              # CLI 入口（直接执行）
├── config.example.yaml   # 配置模板
├── config.yaml           # 你的配置（gitignore）
├── requirements.txt      # Python 依赖
├── .gitignore
├── scripts/
│   ├── label_tool/       # 核心引擎包
│   └── ...
└── README.md
```
