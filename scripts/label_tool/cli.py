"""
label_tool — 通用 AI 数据标注 CLI 工具

用法:
  # 基本标注
  python3 label_tool.py \
    --config config.yaml \
    --spec annotation_spec.yaml \
    --input data.csv \
    --output results.jsonl
  
  # 并发标注，10 线程
  python3 label_tool.py \
    --config config.yaml \
    --spec spec.yaml \
    --input data.jsonl \
    --output results.csv \
    --workers 10 \
    --model gpt-4o
  
  # 断点续标
  python3 label_tool.py \
    --config config.yaml \
    --spec spec.yaml \
    --input data.csv \
    --output results.jsonl \
    --resume

  # 顺序模式（单线程）
  python3 label_tool.py \
    --config config.yaml \
    --spec spec.yaml \
    --input data.csv \
    --output results.jsonl \
    --workers 1 \
    --output-format jsonl
"""
import argparse
import json
import sys
import time
from pathlib import Path

from .config_loader import load_config, get_model_info
from .spec_parser import load_spec
from .input_loader import load_input
from .api_client import APIClient
from .engine import run_sync, build_prompt_template
from .output_writer import write_output


def print_progress(done, total):
    """进度条显示"""
    pct = done / total * 100 if total > 0 else 100
    bar_len = 30
    filled = int(bar_len * done // total) if total > 0 else bar_len
    bar = '█' * filled + '░' * (bar_len - filled)
    sys.stdout.write(f'\r标注进度: [{bar}] {done}/{total} ({pct:.1f}%)')
    sys.stdout.flush()
    if done >= total:
        print()


def setup_argparse():
    parser = argparse.ArgumentParser(
        description="通用 AI 数据标注工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 并发标注 (5 线程)
  python3 label_tool.py --config config.yaml --spec spec.yaml \\
    --input data.csv --output results.jsonl --workers 5

  # 断点续标
  python3 label_tool.py --config config.yaml --spec spec.yaml \\
    --input data.csv --output results.jsonl --resume

  # 指定模型
  python3 label_tool.py --config config.yaml --spec spec.yaml \\
    --input data.csv --output results.jsonl --model gpt-4o
        """
    )
    
    # 输入输出
    parser.add_argument("--config", required=True,
                        help="配置文件路径 (YAML)，包含模型 URL 和密钥")
    parser.add_argument("--spec", required=True,
                        help="标注规范文件路径 (YAML 或 Markdown)")
    parser.add_argument("--input", required=True,
                        help="输入数据文件 (.csv 或 .jsonl)")
    parser.add_argument("--output", default=None,
                        help="输出文件路径 (.csv 或 .jsonl)。默认: 输入文件名_annotated.{ext}")
    
    # 模型配置
    parser.add_argument("--model", default=None,
                        help="指定模型名（覆盖配置中的 default）")
    
    # 标注模式
    parser.add_argument("--workers", type=int, default=5,
                        help="并发线程数 (默认: 5, 1=顺序模式)")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="每批处理记录数（用于限速，默认: 50）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续标：跳过已标注的记录")
    parser.add_argument("--id-field", default=None,
                        help="用作 source_id 的字段名（用于断点续传）")
    
    # 输出
    parser.add_argument("--output-format", choices=["auto", "csv", "jsonl"], default="auto",
                        help="输出格式。auto=根据后缀自动检测（默认）")
    parser.add_argument("--no-raw", action="store_true",
                        help="输出时不包含原始字段（仅标注结果）")
    
    return parser


def main():
    parser = setup_argparse()
    args = parser.parse_args()
    
    # 设置输出路径
    output_path = args.output
    if output_path is None:
        base = Path(args.input).stem
        ext = ".jsonl"  # 默认输出 JSONL
        output_path = f"{base}_annotated{ext}"
    
    print("=" * 60)
    print("  label_tool — 通用 AI 数据标注工具")
    print("=" * 60)
    
    # 1. 加载配置
    print(f"\n[1/5] 加载配置: {args.config}")
    config = load_config(args.config)
    provider_name, provider_cfg, model_cfg = get_model_info(config, args.model)
    print(f"  模型: {model_cfg['name']} ({provider_name})")
    print(f"  地址: {provider_cfg['base_url']}")
    print(f"  并发: {args.workers} 线程")
    
    # 2. 加载 Spec
    print(f"\n[2/5] 加载标注规范: {args.spec}")
    spec = load_spec(args.spec)
    schema_fields = len(spec.get("schema", []))
    system_prompt = spec.get("system_prompt", "")
    print(f"  标注字段: {schema_fields} 个")
    if system_prompt:
        print(f"  系统提示: {system_prompt[:80]}...")
    
    # 3. 加载数据
    print(f"\n[3/5] 加载数据: {args.input}")
    records = load_input(args.input, id_field=args.id_field)
    print(f"  共 {len(records)} 条记录")
    
    # 4. 断点续传检查
    if args.resume and Path(output_path).exists():
        print(f"\n  断点续传模式: 检查已有标注结果...")
        from .output_writer import write_jsonl, write_csv
        # 读取已标注的 source_id
        existing_ids = set()
        ext = Path(output_path).suffix.lower()
        with open(output_path, "r", encoding="utf-8-sig" if ext == ".csv" else "utf-8") as f:
            if ext == ".jsonl":
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
            else:
                import csv as csv_mod
                reader = csv_mod.DictReader(f)
                for row in reader:
                    sid = row.get("_source_id")
                    if sid:
                        existing_ids.add(sid)
        
        done_count = len(existing_ids)
        print(f"  已有 {done_count}/{len(records)} 条标注完成")
        print(f"  需继续标注: {len(records) - done_count} 条")
    
    # 5. 初始化 API 客户端
    print(f"\n[4/5] 初始化 API 客户端...")
    api_client = APIClient(provider_cfg, model_cfg)
    
    # 6. 执行标注
    print(f"\n[5/5] 开始标注...")
    start_time = time.time()
    
    try:
        records = run_sync(
            records, api_client, spec,
            workers=args.workers,
            batch_size=args.batch_size,
            progress_callback=print_progress,
        )
    finally:
        if hasattr(api_client, 'close'):
            import asyncio
            try:
                asyncio.run(api_client.close())
            except RuntimeError:
                pass
    
    elapsed = time.time() - start_time
    done = sum(1 for r in records if r.status == "done")
    errors = sum(1 for r in records if r.status == "error")
    
    # 写入输出文件
    write_output(records, output_path, append=False)
    
    print(f"\n{'=' * 60}")
    print(f"  标注完成!")
    print(f"  成功: {done} 条")
    print(f"  失败: {errors} 条")
    print(f"  耗时: {elapsed:.1f} 秒 ({elapsed/max(done,1):.2f} 秒/条)")
    print(f"  输出: {output_path}")
    print(f"{'=' * 60}")
    
    return 0 if errors == len(records) else 0  # 部分成功也算成功


if __name__ == "__main__":
    sys.exit(main())
