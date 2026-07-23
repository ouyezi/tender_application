from __future__ import annotations

import html
import re

from app.interpret_html_schema import (
    ChecklistSection,
    InterpretHtmlReportData,
    KeyInfoBlock,
    OverviewRow,
    RiskItem,
    ScoringRow,
    TaskItem,
    TasksBlock,
)

INTERPRET_HTML_CSS = """
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f4f6f9; color: #2c3e50; font-size: 14px; }

  .topbar { background: #1a3a5c; color: #fff; padding: 14px 24px; display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }
  .topbar h1 { font-size: 16px; font-weight: 600; }
  .topbar .meta { font-size: 12px; opacity: 0.75; }
  .save-status { font-size: 12px; background: rgba(255,255,255,0.15); padding: 4px 10px; border-radius: 12px; }
  .save-status.saved { color: #7effa0; }

  .container { max-width: 1100px; margin: 0 auto; padding: 20px 16px 60px; }

  .card { background: #fff; border-radius: 8px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); overflow: hidden; }
  .card-header { padding: 12px 18px; font-weight: 600; font-size: 14px; display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none; background: #f8fafc; border-bottom: 1px solid #edf0f5; }
  .card-header .icon { font-size: 16px; }
  .card-header .toggle { margin-left: auto; color: #999; font-size: 12px; transition: transform 0.2s; }
  .card-header.collapsed .toggle { transform: rotate(-90deg); }
  .card-body { padding: 16px 18px; }
  .card-body.hidden { display: none; }

  .progress-bar-wrap { background: #fff; border-radius: 8px; padding: 14px 18px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); display: flex; align-items: center; gap: 14px; position: sticky; top: 66px; z-index: 90; }
  .progress-bar-wrap label { font-size: 13px; color: #666; white-space: nowrap; }
  .progress-bar { flex: 1; height: 8px; background: #edf0f5; border-radius: 4px; overflow: hidden; }
  .progress-fill { height: 100%; background: linear-gradient(90deg, #2980b9, #27ae60); border-radius: 4px; transition: width 0.4s; }
  .progress-pct { font-size: 13px; font-weight: 600; color: #2980b9; min-width: 36px; text-align: right; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { background: #f0f4f8; color: #555; font-weight: 600; padding: 8px 12px; text-align: left; border-bottom: 1px solid #e4e9f0; }
  td { padding: 8px 12px; border-bottom: 1px solid #f0f2f5; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .field-name { color: #666; width: 130px; }
  .field-value { font-weight: 500; }

  .risk-list { list-style: none; }
  .risk-list li { padding: 8px 12px; margin-bottom: 6px; border-radius: 6px; font-size: 13px; display: flex; gap: 8px; align-items: flex-start; }
  .risk-list li.high { background: #fde8e8; border-left: 3px solid #c0392b; }
  .risk-list li.mid  { background: #fef6e4; border-left: 3px solid #e67e22; }
  .risk-list li.low  { background: #e8f4fd; border-left: 3px solid #2980b9; }
  .risk-icon { font-size: 15px; flex-shrink: 0; margin-top: 1px; }
  .risk-content .title { font-weight: 600; }
  .risk-content .desc  { color: #666; margin-top: 2px; }

  .task-group { margin-bottom: 16px; }
  .task-group-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; padding: 4px 8px; border-radius: 4px; }
  .task-group-title.p0 { background: #fde8e8; color: #c0392b; }
  .task-group-title.p1 { background: #fef6e4; color: #d68910; }
  .task-group-title.p2 { background: #e8f4fd; color: #2980b9; }
  .task-row { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; border-radius: 6px; margin-bottom: 4px; background: #f8fafc; border: 1px solid #edf0f5; }
  .task-row input[type=checkbox] { margin-top: 2px; width: 15px; height: 15px; cursor: pointer; accent-color: #2980b9; flex-shrink: 0; }
  .task-row.checked { opacity: 0.6; }
  .task-row.checked .task-name { text-decoration: line-through; color: #999; }
  .task-info { flex: 1; }
  .task-name { font-size: 13px; font-weight: 500; }
  .task-meta { font-size: 11px; color: #999; margin-top: 3px; }

  .checklist-section { margin-bottom: 14px; }
  .checklist-section-title { font-size: 12px; font-weight: 600; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; padding-bottom: 4px; border-bottom: 1px solid #edf0f5; }
  .check-item { display: flex; align-items: flex-start; gap: 10px; padding: 7px 10px; border-radius: 5px; margin-bottom: 3px; background: #f8fafc; }
  .check-item input[type=checkbox] { margin-top: 2px; width: 15px; height: 15px; cursor: pointer; accent-color: #27ae60; flex-shrink: 0; }
  .check-item.checked { opacity: 0.55; }
  .check-item.checked .check-label { text-decoration: line-through; color: #999; }
  .check-item.redline { background: #fde8e8; }
  .check-label { flex: 1; font-size: 13px; }

  .info-block { margin-bottom: 14px; }
  .info-block h4 { font-size: 13px; font-weight: 600; color: #444; margin-bottom: 8px; }
  .timeline-row { display: flex; gap: 10px; padding: 6px 10px; border-radius: 5px; font-size: 13px; border-bottom: 1px solid #f0f2f5; }
  .timeline-row:last-child { border-bottom: none; }
  .timeline-label { color: #666; width: 160px; flex-shrink: 0; }
  .timeline-value { font-weight: 500; }
  .timeline-note { color: #999; font-size: 12px; }

  .strategy-block { margin-bottom: 14px; }
  .strategy-block h4 { font-size: 13px; font-weight: 600; color: #1a3a5c; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
  .strategy-content { background: #f8fafc; border-radius: 6px; padding: 12px 14px; font-size: 13px; line-height: 1.8; color: #444; border-left: 3px solid #2980b9; }
  .strategy-content.advantage { border-left-color: #27ae60; }
  .strategy-content.risk-avoid { border-left-color: #e67e22; }
  .strategy-content.price { border-left-color: #8e44ad; }

  .score-table td { font-size: 13px; }
  .score-bar-wrap { display: flex; align-items: center; gap: 8px; }
  .score-bar { height: 6px; background: #edf0f5; border-radius: 3px; flex: 1; overflow: hidden; }
  .score-bar-fill { height: 100%; background: #2980b9; border-radius: 3px; }
  .score-num { font-weight: 600; color: #2980b9; min-width: 32px; }

  .section-divider { display: flex; align-items: center; gap: 10px; margin: 6px 0 12px; color: #bbb; font-size: 11px; }
  .section-divider::before, .section-divider::after { content: ''; flex: 1; height: 1px; background: #edf0f5; }

  @media print {
    .topbar { position: static; }
    .progress-bar-wrap { position: static; }
    .card-body.hidden { display: block !important; }
  }
"""

INTERPRET_HTML_JS = """
function toggleCard(header) {
  const body = header.nextElementSibling;
  const collapsed = body.classList.toggle('hidden');
  header.classList.toggle('collapsed', collapsed);
}

window.addEventListener('DOMContentLoaded', () => {
  updateProgress();
  document.querySelectorAll('input[type=checkbox]').forEach(cb => {
    cb.addEventListener('change', function() {
      const item = this.closest('.check-item, .task-row');
      if (item) item.classList.toggle('checked', this.checked);
      updateProgress();
    });
  });
});

function updateProgress() {
  const all = document.querySelectorAll('.check-item input[type=checkbox]');
  if (all.length === 0) return;
  const done = [...all].filter(cb => cb.checked).length;
  const pct = Math.round(done / all.length * 100);
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressPct').textContent = pct + '%';
}
"""

_RISK_ICONS = {"high": "🔴", "mid": "🟡", "low": "🔵"}

_TASK_GROUP_TITLES = {
    "p0": "🔴 P0 — 阻塞性任务（必须完成，否则无法投标）",
    "p1": "🟡 P1 — 重要任务（影响中标率）",
    "p2": "🔵 P2 — 优化任务（提升竞争力）",
}


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def _rich_text(text: str) -> str:
    escaped = _escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return escaped.replace("\n", "<br>\n")


def _render_card(icon: str, title: str, body: str) -> str:
    return (
        '<div class="card">\n'
        f'  <div class="card-header" onclick="toggleCard(this)">\n'
        f'    <span class="icon">{icon}</span> {title}\n'
        '    <span class="toggle">▼</span>\n'
        "  </div>\n"
        f'  <div class="card-body">\n{body}\n  </div>\n'
        "</div>"
    )


def _render_overview_rows(rows: list[OverviewRow]) -> str:
    parts = ["<table>"]
    for row in rows:
        if row.colspan:
            parts.append(
                "<tr>"
                f'<td class="field-name">{_escape(row.label)}</td>'
                f'<td class="field-value" colspan="{row.colspan}">{_escape(row.value)}</td>'
                "</tr>"
            )
        else:
            parts.append(
                "<tr>"
                f'<td class="field-name">{_escape(row.label)}</td>'
                f'<td class="field-value">{_escape(row.value)}</td>'
                f'<td class="field-name">{_escape(row.label2 or "")}</td>'
                f'<td class="field-value">{_escape(row.value2 or "")}</td>'
                "</tr>"
            )
    parts.append("</table>")
    return "\n".join(parts)


def _render_risks(risks: list[RiskItem]) -> str:
    if not risks:
        return '<ul class="risk-list"></ul>'
    items = []
    for risk in risks:
        icon = _RISK_ICONS.get(risk.level, "🔵")
        items.append(
            f'<li class="{risk.level}"><span class="risk-icon">{icon}</span>'
            f'<div class="risk-content"><div class="title">{_escape(risk.title)}</div>'
            f'<div class="desc">{_escape(risk.desc)}</div></div></li>'
        )
    return '<ul class="risk-list">\n' + "\n".join(items) + "\n</ul>"


def _render_task_item(task: TaskItem) -> str:
    meta_parts = []
    if task.owner:
        meta_parts.append(f"负责人：{_escape(task.owner)}")
    if task.deadline:
        meta_parts.append(f"截止：{_escape(task.deadline)}")
    meta = " | ".join(meta_parts)
    meta_html = f'<div class="task-meta">{meta}</div>' if meta else ""
    return (
        '<div class="task-row"><input type="checkbox">'
        f'<div class="task-info"><div class="task-name">{_escape(task.name)}</div>{meta_html}</div>'
        "</div>"
    )


def _render_tasks(tasks: TasksBlock) -> str:
    groups = []
    for key in ("p0", "p1", "p2"):
        items = getattr(tasks, key)
        rows = "".join(_render_task_item(item) for item in items)
        groups.append(
            '<div class="task-group">'
            f'<div class="task-group-title {key}">{_TASK_GROUP_TITLES[key]}</div>'
            f"{rows}"
            "</div>"
        )
    return "\n".join(groups)


def _render_checklist(sections: list[ChecklistSection]) -> str:
    parts = []
    for section in sections:
        title_style = ' style="color:#c0392b;"' if section.redline else ""
        parts.append(
            '<div class="checklist-section">'
            f'<div class="checklist-section-title"{title_style}>{_escape(section.section)}</div>'
        )
        for item in section.items:
            redline_class = " redline" if section.redline else ""
            parts.append(
                f'<div class="check-item{redline_class}"><input type="checkbox">'
                f'<div class="check-label">{_escape(item)}</div></div>'
            )
        parts.append("</div>")
    return "\n".join(parts)


def _render_key_value_table(rows: list) -> str:
    if not rows:
        return "<table></table>"
    body = "".join(
        f"<tr><td class=\"field-name\">{_escape(row.label)}</td>"
        f"<td>{_escape(row.value)}</td></tr>"
        for row in rows
    )
    return f"<table>{body}</table>"


def _render_key_info(key_info: KeyInfoBlock) -> str:
    parts = ['<div class="info-block"><h4>时间节点</h4>']
    for row in key_info.timeline:
        parts.append(
            '<div class="timeline-row">'
            f'<span class="timeline-label">{_escape(row.label)}</span>'
            f'<span class="timeline-value">{_escape(row.value)}</span>'
            f'<span class="timeline-note">{_escape(row.note)}</span>'
            "</div>"
        )
    parts.append("</div>")
    for label, rows in (
        ("资质要求", key_info.qualification),
        ("商务条款", key_info.commercial),
        ("技术要求", key_info.technical),
    ):
        parts.append(f'<div class="section-divider">{label}</div>')
        parts.append(f'<div class="info-block">{_render_key_value_table(rows)}</div>')
    return "\n".join(parts)


def _render_strategy(data: InterpretHtmlReportData) -> str:
    blocks = [
        ("💪 优势强化", "advantage", data.strategy.advantage),
        ("🛡️ 风险规避", "risk-avoid", data.strategy.risk_avoid),
        ("💰 报价策略", "price", data.strategy.price),
    ]
    parts = []
    for heading, css_class, content in blocks:
        if not content.strip():
            continue
        parts.append(
            '<div class="strategy-block">'
            f"<h4>{heading}</h4>"
            f'<div class="strategy-content {css_class}">{_rich_text(content)}</div>'
            "</div>"
        )
    return "\n".join(parts)


def _score_bar_width(weight: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)", weight)
    if not match:
        return "10%"
    return f"{match.group(1)}%"


def _render_scoring(rows: list[ScoringRow]) -> str:
    if not rows:
        return (
            '<table class="score-table"><thead>'
            "<tr><th>评分维度</th><th>分值</th><th>占比</th><th>评分标准</th><th>得分策略</th></tr>"
            "</thead><tbody></tbody></table>"
        )
    body_parts = []
    for row in rows:
        width = _score_bar_width(row.weight)
        body_parts.append(
            "<tr>"
            f"<td>{_escape(row.dimension)}</td>"
            "<td>"
            '<div class="score-bar-wrap">'
            '<div class="score-bar">'
            f'<div class="score-bar-fill" style="width:{width}"></div>'
            "</div>"
            f'<span class="score-num">{_escape(row.score)}</span>'
            "</div></td>"
            f"<td>{_escape(row.weight)}</td>"
            f"<td>{_escape(row.criteria)}</td>"
            f"<td>{_escape(row.strategy)}</td>"
            "</tr>"
        )
    return (
        '<table class="score-table"><thead>'
        "<tr><th>评分维度</th><th>分值</th><th>占比</th><th>评分标准</th><th>得分策略</th></tr>"
        "</thead><tbody>"
        + "".join(body_parts)
        + "</tbody></table>"
    )


def render_interpret_html_report(data: InterpretHtmlReportData, *, task_id: str) -> str:
    project_key = data.meta.project_key or f"tender_{task_id}"
    cards = [
        _render_card("📋", "一、项目速览", _render_overview_rows(data.overview.rows)),
        _render_card("⚠️", "二、风险雷达", _render_risks(data.risks)),
        _render_card("📅", "三、投标任务清单", _render_tasks(data.tasks)),
        _render_card("✅", "四、投标检查清单", _render_checklist(data.checklist)),
        _render_card("🔑", "五、关键信息摘录", _render_key_info(data.key_info)),
        _render_card("🎯", "六、投标策略建议", _render_strategy(data)),
        _render_card("📊", "七、评分要点分析", _render_scoring(data.scoring)),
        _render_card(
            "📝",
            "八、团队备注",
            '<textarea style="width:100%;border:1px solid #e0e6ed;border-radius:6px;padding:10px 14px;'
            'font-size:13px;color:#444;resize:vertical;min-height:80px;background:#fff;font-family:inherit;" '
            'placeholder="在此记录团队讨论、决策、临时注意事项..."></textarea>',
        ),
    ]
    body = (
        '<div class="topbar">\n'
        "  <div>\n"
        f"    <h1>{_escape(data.meta.title)}</h1>\n"
        f'    <div class="meta">{_escape(data.meta.subtitle)}</div>\n'
        "  </div>\n"
        '  <div class="save-status saved">✓ 已保存</div>\n'
        "</div>\n"
        '<div class="container">\n'
        '  <div class="progress-bar-wrap">\n'
        "    <label>检查清单完成度</label>\n"
        '    <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>\n'
        '    <div class="progress-pct" id="progressPct">0%</div>\n'
        "  </div>\n"
        + "\n".join(f"  {card}" for card in cards)
        + "\n</div>"
    )
    js = f"const PROJECT_KEY = '{_escape(project_key)}';\n\n{INTERPRET_HTML_JS.strip()}"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_escape(data.meta.title)}</title>\n"
        f"<style>{INTERPRET_HTML_CSS}\n</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        f"<script>\n{js}\n</script>\n"
        "</body>\n"
        "</html>\n"
    )
