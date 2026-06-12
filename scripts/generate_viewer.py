#!/usr/bin/env python3
"""
generate_viewer.py — 一键生成独立的标注数据 Web 查看器

用法:
  python3 generate_viewer.py <data_dir> [output_html]

功能:
1. 读取 data_dir/results/dataset.jsonl 中的标注结果
2. 读取 data_dir/samples.jsonl 中的原始数据
3. 生成一个独立的 HTML 文件，内嵌所有数据
4. 直接用浏览器打开即可查看，无需启动任何服务

输出:
  默认: results/viewer.html（内嵌在数据目录中）
  也可指定: python3 generate_viewer.py /path/to/data /path/to/custom.html
"""

import json
import os
import sys
import argparse
import html as html_module


def load_jsonl(filepath):
    """加载 JSONL 文件"""
    records = []
    if not os.path.exists(filepath):
        return records
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records


def generate_html(records, output_path):
    """生成独立 HTML 查看器"""
    # 统计数据
    total = len(records)
    safe_count = sum(1 for r in records if r.get("is_safe") is True)
    unsafe_count = sum(1 for r in records if r.get("is_safe") is False)
    pass_count = sum(1 for r in records if r.get("label") == "pass")
    reject_count = sum(1 for r in records if r.get("label") == "reject")

    # 转义 HTML
    escaped_records = []
    for r in records:
        esc = {}
        for k, v in r.items():
            if isinstance(v, str):
                esc[k] = html_module.escape(v)
            else:
                esc[k] = v
        escaped_records.append(esc)

    records_json = json.dumps(escaped_records, ensure_ascii=False, indent=2)

    html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>数据标注查看器</title>
<style>
:root {{
  --sidebar-w: 300px;
  --bg: #f5f6f8;
  --card: #ffffff;
  --border: #e2e5ea;
  --text: #1a1a2e;
  --text2: #6b7280;
  --accent: #4f8cff;
  --success: #34c759;
  --danger: #ff3b30;
  --warning: #ff9500;
  --radius: 8px;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }}

/* Top Bar */
.topbar {{ height: 52px; background: var(--card); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 20px; flex-shrink: 0; }}
.topbar-title {{ font-size: 1.05rem; font-weight: 600; }}
.stats {{ display: flex; gap: 12px; font-size: 0.82rem; color: var(--text2); }}
.stats span {{ background: #f0f2f5; padding: 3px 10px; border-radius: 12px; }}
.stats .safe {{ color: var(--success); }}
.stats .unsafe {{ color: var(--danger); }}

/* Main Layout */
.main {{ display: flex; flex: 1; overflow: hidden; }}

/* Sidebar */
.sidebar {{ width: var(--sidebar-w); background: var(--card); border-right: 1px solid var(--border); overflow-y: auto; flex-shrink: 0; }}
.sidebar-header {{ padding: 12px 16px; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text2); font-weight: 600; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--card); z-index: 1; }}
.sidebar-list {{ list-style: none; }}
.sidebar-item {{ padding: 10px 16px; cursor: pointer; border-bottom: 1px solid #f0f2f5; display: flex; align-items: center; gap: 8px; font-size: 0.85rem; transition: background 0.15s; }}
.sidebar-item:hover {{ background: #f5f7fa; }}
.sidebar-item.active {{ background: #e8f0fe; color: var(--accent); font-weight: 500; }}
.sidebar-item .status-dot {{ width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }}
.sidebar-item .status-dot.safe {{ background: var(--success); }}
.sidebar-item .status-dot.unsafe {{ background: var(--danger); }}
.sidebar-item .status-dot.pending {{ background: var(--warning); }}
.sidebar-item .item-id {{ font-weight: 500; margin-right: 4px; }}
.sidebar-item .item-preview {{ color: var(--text2); font-size: 0.78rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }}

/* Content Area */
.content {{ flex: 1; overflow-y: auto; padding: 24px; }}
.empty-state {{ text-align: center; color: var(--text2); margin-top: 100px; font-size: 1.1rem; }}

/* Record Card */
.record-card {{ max-width: 900px; margin: 0 auto; }}
.card-section {{ background: var(--card); border-radius: var(--radius); border: 1px solid var(--border); padding: 20px; margin-bottom: 16px; }}
.card-section-title {{ font-size: 0.85rem; font-weight: 600; color: var(--text2); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
.card-section-title .icon {{ font-size: 1rem; }}

.field-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }}
.field-item {{ padding: 12px; background: #f8f9fa; border-radius: 6px; }}
.field-label {{ font-size: 0.75rem; color: var(--text2); text-transform: uppercase; margin-bottom: 4px; }}
.field-value {{ font-size: 0.95rem; font-weight: 500; word-break: break-all; }}
.field-value.tag {{ color: var(--accent); }}
.field-value.safe {{ color: var(--success); }}
.field-value.unsafe {{ color: var(--danger); }}

.prompt-block {{ background: #f8f9fa; border-radius: 6px; padding: 16px; font-size: 0.9rem; line-height: 1.6; white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; }}

.meta-info {{ font-size: 0.8rem; color: var(--text2); }}
.meta-info span {{ margin-right: 16px; }}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-title">📋 数据标注查看器</div>
  <div class="stats">
    <span>总计: {total}</span>
    <span class="safe">✅ 安全: {safe_count}</span>
    <span class="unsafe">❌ 不安全: {unsafe_count}</span>
    <span>✅ Pass: {pass_count}</span>
    <span>❌ Reject: {reject_count}</span>
  </div>
</div>

<div class="main">
  <div class="sidebar">
    <div class="sidebar-header">样本列表 ({total})</div>
    <ul class="sidebar-list" id="sidebarList"></ul>
  </div>
  <div class="content">
    <div class="empty-state" id="emptyState">选择一个样本查看详情</div>
    <div class="record-card" id="recordCard" style="display:none;"></div>
  </div>
</div>

<script>
const RECORDS = {records_json};
const TOTAL = RECORDS.length;

// Build sidebar
const sidebarList = document.getElementById('sidebarList');
RECORDS.forEach((rec, i) => {{
  const li = document.createElement('li');
  li.className = 'sidebar-item';
  li.dataset.index = i;
  const sid = rec.source_id || ('样本 ' + (i + 1));
  const prompt_preview = (rec.original_prompt || '').substring(0, 40);
  const statusClass = rec.is_safe === true ? 'safe' : rec.is_safe === false ? 'unsafe' : 'pending';
  li.innerHTML = `
    <span class="status-dot ${{statusClass}}"></span>
    <span class="item-id">#${{i + 1}}</span>
    <span class="item-preview">${{sid}}: ${{prompt_preview}}</span>
  `;
  li.addEventListener('click', () => showRecord(i));
  sidebarList.appendChild(li);
}});

function showRecord(idx) {{
  const rec = RECORDS[idx];
  // Highlight sidebar
  document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
  sidebarList.children[idx].classList.add('active');

  // Show card
  document.getElementById('emptyState').style.display = 'none';
  const card = document.getElementById('recordCard');
  card.style.display = 'block';

  const safeClass = rec.is_safe === true ? 'safe' : rec.is_safe === false ? 'unsafe' : '';
  const safeText = rec.is_safe === true ? '✅ 安全' : rec.is_safe === false ? '❌ 不安全' : '⏳ 未标注';

  const riskTags = (rec.risk_tags || []).map(t => `<span class="field-value tag">$${{html_module.escape(t)}}$</span>`).join(' ');

  card.innerHTML = `
    <!-- Meta -->
    <div class="card-section">
      <div class="card-section-title"><span class="icon">ℹ️</span> 基本信息</div>
      <div class="meta-info">
        <span>Source ID: $${{html_module.escape(rec.source_id || '')}}</span>
        <span>时间: $${{html_module.escape(rec.annotation_time || '')}}</span>
        <span>Tokens: $${{rec.tokens_used || '-'}}</span>
      </div>
    </div>

    <!-- Schema A: 安全标签 -->
    <div class="card-section">
      <div class="card-section-title"><span class="icon">🛡️</span> 安全标签 (Schema A)</div>
      <div class="field-grid">
        <div class="field-item">
          <div class="field-label">是否安全</div>
          <div class="field-value ${{safeClass}}">$${{safeText}}</div>
        </div>
        <div class="field-item">
          <div class="field-label">风险标签</div>
          <div class="field-value">$${{riskTags || '<span style="color:var(--text2)">无</span>'}}</div>
        </div>
        <div class="field-item">
          <div class="field-label">严重程度</div>
          <div class="field-value">$${{rec.severity_num ?? '未标注'}}</div>
        </div>
      </div>
    </div>

    <!-- Schema B: 训练可用性 -->
    <div class="card-section">
      <div class="card-section-title"><span class="icon">📝</span> 训练可用性 (Schema B)</div>
      <div class="field-grid">
        <div class="field-item">
          <div class="field-label">标注标签</div>
          <div class="field-value">$${{html_module.escape(rec.label || '未标注')}}</div>
        </div>
        <div class="field-item">
          <div class="field-label">标注理由</div>
          <div class="field-value">$${{html_module.escape(rec.reason || '')}}</div>
        </div>
        <div class="field-item">
          <div class="field-label">需人工审核</div>
          <div class="field-value">$${{rec.needs_human_review ? '是' : '否'}}</div>
        </div>
      </div>
    </div>

    <!-- Original Prompt -->
    <div class="card-section">
      <div class="card-section-title"><span class="icon">💬</span> 原始提问</div>
      <div class="prompt-block">$${{html_module.escape(rec.original_prompt || '')}}</div>
    </div>

    <!-- Original Completion -->
    <div class="card-section">
      <div class="card-section-title"><span class="icon">🤖</span> 原始回答</div>
      <div class="prompt-block">$${{html_module.escape(rec.original_completion || '')}}</div>
    </div>
  `;
}}
</script>
</body>
</html>
'''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return {
        "total": total,
        "safe": safe_count,
        "unsafe": unsafe_count,
        "pass": pass_count,
        "reject": reject_count,
        "output": output_path,
    }


def main():
    parser = argparse.ArgumentParser(
        description="一键生成独立的标注数据 Web 查看器"
    )
    parser.add_argument("data_dir", help="数据目录路径")
    parser.add_argument(
        "output_html",
        nargs="?",
        default=None,
        help="输出 HTML 路径（默认 results/viewer.html）",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.data_dir):
        print(f"错误: 数据目录不存在: {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    results_dir = os.path.join(args.data_dir, "results")
    if not os.path.exists(results_dir):
        print(f"错误: results 目录不存在: {results_dir}", file=sys.stderr)
        print(f"请先运行 batch_annotate.py 生成标注数据", file=sys.stderr)
        sys.exit(1)

    dataset_path = os.path.join(results_dir, "dataset.jsonl")
    if not os.path.exists(dataset_path):
        print(f"错误: 没有找到标注数据: {dataset_path}", file=sys.stderr)
        sys.exit(1)

    # 输出路径
    if args.output_html:
        output_path = args.output_html
    else:
        output_path = os.path.join(results_dir, "viewer.html")

    # 加载数据
    print(f"读取标注数据: {dataset_path}", file=sys.stderr)
    records = load_jsonl(dataset_path)

    if not records:
        print(f"错误: dataset.jsonl 为空", file=sys.stderr)
        sys.exit(1)

    print(f"共 {len(records)} 条标注记录", file=sys.stderr)

    # 生成 HTML
    stats = generate_html(records, output_path)
    print(f"\n✅ 查看器已生成!", file=sys.stderr)
    print(f"   文件: {output_path}", file=sys.stderr)
    print(f"   总计: {stats['total']} | 安全: {stats['safe']} | 不安全: {stats['unsafe']}", file=sys.stderr)
    print(f"   Pass: {stats['pass']} | Reject: {stats['reject']}", file=sys.stderr)
    print(f"\n👉 直接用浏览器打开: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
