from __future__ import annotations

import html
import re

from app.config import REPORT_DIR
from app.engine.base import InterpretationResult
from app.services import artifact


def markdown_to_html_document(title: str, markdown: str) -> str:
    body_parts: list[str] = []
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        if line.startswith("# "):
            body_parts.append(f"<h1>{html.escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            body_parts.append(f"<h2>{html.escape(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            body_parts.append(f"<h3>{html.escape(line[4:].strip())}</h3>")
        elif line.startswith("- "):
            body_parts.append(f"<li>{html.escape(line[2:].strip())}</li>")
        elif line.strip() == "":
            continue
        else:
            escaped = html.escape(line)
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
            body_parts.append(f"<p>{escaped}</p>")

    wrapped: list[str] = []
    in_list = False
    for part in body_parts:
        if part.startswith("<li>"):
            if not in_list:
                wrapped.append("<ul>")
                in_list = True
            wrapped.append(part)
        else:
            if in_list:
                wrapped.append("</ul>")
                in_list = False
            wrapped.append(part)
    if in_list:
        wrapped.append("</ul>")

    body = "\n".join(wrapped)
    safe_title = html.escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<title>{safe_title}</title>\n"
        "<style>body{font-family:sans-serif;max-width:800px;margin:2rem auto;line-height:1.6;}"
        "h1,h2,h3{margin-top:1.4em;}ul{padding-left:1.2em;}</style>\n"
        "</head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def save_interpret_reports(task_id: str, result: InterpretationResult) -> str:
    out_dir = REPORT_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "interpret.md"
    md_path.write_text(result.markdown, encoding="utf-8")
    artifact.sync_to_artifact_report(task_id, md_path)
    return str(md_path)
