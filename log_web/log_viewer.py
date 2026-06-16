"""将 JSONL 日志文件转换为可读的 HTML 页面。

用法:
    python -m ccefix_proxy.log_viewer logs/session_xxx.jsonl
    python -m ccefix_proxy.log_viewer logs/  # 转换目录下所有 session_*.jsonl
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from html import escape
from pathlib import Path

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-deep: #0a0e17;
  --bg-surface: #111827;
  --bg-elevated: #1a2332;
  --bg-hover: #1e293b;
  --border: #1e2d3d;
  --border-accent: #2dd4bf30;
  --text-primary: #e2e8f0;
  --text-secondary: #64748b;
  --text-muted: #475569;
  --accent: #2dd4bf;
  --accent-glow: #2dd4bf40;
  --cyan: #22d3ee;
  --amber: #fbbf24;
  --rose: #fb7185;
  --violet: #a78bfa;
  --green: #34d399;
  --green-glow: #34d39925;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html { font-size: 14px; }
body {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
  background: var(--bg-deep);
  color: var(--text-primary);
  min-height: 100vh;
  overflow-x: hidden;
}

/* ── Noise overlay ── */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='.025'/%3E%3C/svg%3E");
  pointer-events: none;
  z-index: 0;
}

/* ── Layout ── */
.app { position: relative; z-index: 1; max-width: 1400px; margin: 0 auto; padding: 32px 24px; }

/* ── Header ── */
.header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}
.header-left {}
.header .logo {
  font-family: 'Outfit', sans-serif;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 2.5px;
  text-transform: uppercase;
  color: var(--accent);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.header .logo::before {
  content: '';
  display: inline-block;
  width: 8px;
  height: 8px;
  background: var(--accent);
  border-radius: 2px;
  box-shadow: 0 0 12px var(--accent-glow);
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
.header h1 {
  font-family: 'Outfit', sans-serif;
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.5px;
}
.header .meta {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
  font-weight: 300;
}

/* ── Stats ── */
.stats {
  display: flex;
  gap: 2px;
  margin-bottom: 28px;
  background: var(--bg-surface);
  border-radius: 12px;
  padding: 4px;
  border: 1px solid var(--border);
  overflow: hidden;
}
.stat {
  flex: 1;
  padding: 14px 20px;
  border-radius: 10px;
  transition: background 0.2s;
  position: relative;
}
.stat:hover { background: var(--bg-hover); }
.stat .num {
  font-family: 'Outfit', sans-serif;
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}
.stat .label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--text-muted);
  margin-top: 6px;
  font-weight: 500;
}
.stat.total .num { color: var(--cyan); }
.stat.edit .num  { color: var(--green); }
.stat.info .num  { color: var(--accent); }
.stat.warn .num  { color: var(--amber); }
.stat.err .num   { color: var(--rose); }

/* ── Toolbar ── */
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.filters {
  display: flex;
  gap: 4px;
  background: var(--bg-surface);
  border-radius: 10px;
  padding: 4px;
  border: 1px solid var(--border);
}
.filters button {
  padding: 6px 16px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 12px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 500;
  letter-spacing: 0.3px;
  transition: all 0.15s;
}
.filters button:hover { color: var(--text-primary); background: var(--bg-hover); }
.filters button.active {
  background: var(--bg-elevated);
  color: var(--accent);
  box-shadow: 0 0 0 1px var(--border-accent);
}
.search-wrap {
  position: relative;
  flex: 0 1 320px;
  margin-left: auto;
}
.search-wrap svg {
  position: absolute;
  left: 12px;
  top: 50%;
  transform: translateY(-50%);
  width: 16px;
  height: 16px;
  color: var(--text-muted);
}
.search-box {
  width: 100%;
  padding: 8px 12px 8px 36px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 13px;
  font-family: 'JetBrains Mono', monospace;
  outline: none;
  transition: border-color 0.2s;
}
.search-box::placeholder { color: var(--text-muted); }
.search-box:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }

/* ── Log table ── */
.log-table {
  background: var(--bg-surface);
  border-radius: 12px;
  border: 1px solid var(--border);
  overflow: hidden;
}
.log-table table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.log-table thead th {
  text-align: left;
  padding: 10px 16px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1.5px;
  color: var(--text-muted);
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 2;
}
.log-table tbody tr {
  border-bottom: 1px solid var(--border);
  transition: background 0.1s;
}
.log-table tbody tr:last-child { border-bottom: none; }
.log-table tbody tr:hover { background: var(--bg-hover); }
.log-table td {
  padding: 8px 16px;
  vertical-align: top;
  line-height: 1.6;
}
.col-time {
  color: var(--text-muted);
  white-space: nowrap;
  font-size: 11.5px;
  font-weight: 400;
  width: 90px;
  padding-right: 20px !important;
}
.col-level {
  width: 70px;
  padding-right: 20px !important;
}
.level-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.level-badge.debug   { background: #33415530; color: #64748b; }
.level-badge.info    { background: #2dd4bf15; color: var(--accent); }
.level-badge.warning { background: #fbbf2415; color: var(--amber); }
.level-badge.error   { background: #fb718515; color: var(--rose); }
.col-msg {
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-all;
  max-width: 900px;
}
tr.debug .col-msg   { color: var(--text-muted); }
tr.info .col-msg    { color: var(--text-primary); }
tr.warning .col-msg { color: var(--amber); }
tr.error .col-msg   { color: var(--rose); }

/* ── Edit Fix highlight ── */
tr.edit-row {
  background: var(--green-glow);
}
tr.edit-row:hover {
  background: #34d39918;
}
tr.edit-row .col-msg {
  color: var(--green);
}
.tag-edit {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  background: #34d39920;
  color: var(--green);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: uppercase;
  margin-right: 8px;
  vertical-align: middle;
}

/* ── Empty state ── */
.empty {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-muted);
  font-size: 14px;
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .app { padding: 16px 12px; }
  .header { flex-direction: column; align-items: flex-start; }
  .header h1 { font-size: 22px; }
  .stats { flex-wrap: wrap; }
  .stat { flex: 1 1 80px; }
  .toolbar { flex-direction: column; align-items: stretch; }
  .search-wrap { flex: 1 1 100%; margin-left: 0; }
}
</style>
</head>
<body>
<div class="app">
  <header class="header">
    <div class="header-left">
      <div class="logo">ccedit-fix-proxy</div>
      <h1>__TITLE__</h1>
      <div class="meta">__META__</div>
    </div>
  </header>

  <div class="stats">__STATS__</div>

  <div class="toolbar">
    <div class="filters">
      <button class="active" onclick="filter('all', this)">All</button>
      <button onclick="filter('info', this)">Info</button>
      <button onclick="filter('warning', this)">Warn</button>
      <button onclick="filter('error', this)">Error</button>
      <button onclick="filter('edit-fix', this)">Edit Fix</button>
    </div>
    <div class="search-wrap">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input class="search-box" placeholder="Search logs..." oninput="search(this.value)">
    </div>
  </div>

  <div class="log-table">
    <table>
      <thead><tr>
        <th>Time</th>
        <th>Level</th>
        <th>Message</th>
      </tr></thead>
      <tbody id="logs">__ROWS__</tbody>
    </table>
  </div>
</div>

<script>
let currentFilter = 'all';
function filter(type, btn) {
  currentFilter = type;
  document.querySelectorAll('.filters button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyFilters();
}
function search(q) {
  applyFilters(q);
}
function applyFilters(q) {
  q = (q || document.querySelector('.search-box').value).toLowerCase();
  document.querySelectorAll('#logs tr').forEach(tr => {
    let show = true;
    if (currentFilter !== 'all' && !tr.classList.contains(currentFilter)) show = false;
    if (show && q && !tr.textContent.toLowerCase().includes(q)) show = false;
    tr.style.display = show ? '' : 'none';
  });
}
</script>
</body>
</html>"""


def jsonl_to_html(jsonl_path: str, html_path: str | None = None) -> str:
    """Convert a JSONL log file to HTML. Returns the output HTML path."""
    jsonl_path = Path(jsonl_path)
    if html_path is None:
        html_path = jsonl_path.with_suffix(".html")

    entries = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not entries:
        print(f"No entries in {jsonl_path}")
        return str(html_path)

    # Stats
    counts: dict[str, int] = {}
    edit_count = 0
    for e in entries:
        lv = e.get("level", "?")
        counts[lv] = counts.get(lv, 0) + 1
        if "[edit-fix]" in e.get("msg", ""):
            edit_count += 1

    stats_html = _build_stats(len(entries), edit_count, counts)
    rows_html = _build_rows(entries)

    session_name = jsonl_path.stem
    title = escape(session_name)
    meta = escape(f"{jsonl_path.name}  ·  {len(entries)} entries")

    html = (
        HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__META__", meta)
        .replace("__STATS__", stats_html)
        .replace("__ROWS__", rows_html)
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  {jsonl_path.name} -> {Path(html_path).name}  ({len(entries)} entries)")
    return str(html_path)


def _build_stats(total: int, edit_count: int, counts: dict[str, int]) -> str:
    stat_css = {"DEBUG": "", "INFO": "info", "WARNING": "warn", "ERROR": "err"}
    parts = [f'<div class="stat total"><div class="num">{total}</div><div class="label">Total</div></div>']
    if edit_count:
        parts.append(f'<div class="stat edit"><div class="num">{edit_count}</div><div class="label">Edit Fix</div></div>')
    for lv, cnt in counts.items():
        css = stat_css.get(lv, "")
        parts.append(f'<div class="stat {css}"><div class="num">{cnt}</div><div class="label">{lv}</div></div>')
    return "".join(parts)


def _build_rows(entries: list[dict]) -> str:
    rows = []
    for e in entries:
        ts_raw = e.get("ts", "")
        try:
            dt = datetime.fromisoformat(ts_raw)
            ts = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            ts = ts_raw

        level = e.get("level", "?")
        msg = escape(e.get("msg", ""))

        row_class = level.lower()
        is_edit = "[edit-fix]" in msg
        if is_edit:
            row_class += " edit-fix edit-row"

        edit_tag = '<span class="tag-edit">EDIT</span>' if is_edit else ""

        rows.append(
            f'<tr class="{row_class}">'
            f'<td class="col-time">{ts}</td>'
            f'<td class="col-level"><span class="level-badge {level.lower()}">{level}</span></td>'
            f'<td class="col-msg">{edit_tag}{msg}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def convert_directory(log_dir: str) -> list[str]:
    """Convert all session_*.jsonl files in a directory."""
    log_dir = Path(log_dir)
    jsonl_files = sorted(log_dir.glob("session_*.jsonl"))
    if not jsonl_files:
        print(f"No session_*.jsonl files in {log_dir}")
        return []

    print(f"Converting {len(jsonl_files)} JSONL file(s) in {log_dir}/")
    results = []
    for f in jsonl_files:
        results.append(jsonl_to_html(f))
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m log_web <file.jsonl | logs_dir>")
        sys.exit(1)

    target = sys.argv[1]
    if os.path.isdir(target):
        convert_directory(target)
    else:
        jsonl_to_html(target)


if __name__ == "__main__":
    main()
