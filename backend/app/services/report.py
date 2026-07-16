from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Optional

from docx import Document
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import db as database
from app.config import REPORT_DIR
from app.models import DiagnosisResult


def build_markdown(task_id: str, results: list[dict]) -> str:
    total = len(results)
    counts = Counter(r.get("result", "") for r in results)
    overview_parts = [f"总计 {total} 项"]
    for label in ("通过", "风险", "缺失"):
        if counts.get(label):
            overview_parts.append(f"{label} {counts[label]} 项")
    for label, n in sorted(counts.items()):
        if label and label not in ("通过", "风险", "缺失"):
            overview_parts.append(f"{label} {n} 项")

    lines = [
        "# 标书诊断报告",
        "",
        f"**任务编号：** {task_id}",
        "",
        "## 概览",
        "",
        "；".join(overview_parts),
        "",
        "## 诊断明细",
        "",
    ]

    for i, item in enumerate(results, start=1):
        title = item.get("content_title") or f"检查项 {i}"
        lines.extend(
            [
                f"### {i}. {title}",
                "",
                f"- **描述：** {item.get('description', '')}",
                f"- **结论：** {item.get('result', '')}",
                f"- **证据：** {item.get('evidence', '')}",
                f"- **建议：** {item.get('suggestion', '')}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def write_docx(path: str, markdown: str) -> None:
    doc = Document()
    for line in markdown.split("\n"):
        if line.startswith("# "):
            doc.add_heading(line[2:].strip(), level=1)
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=2)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=3)
        elif line.strip() == "":
            continue
        else:
            doc.add_paragraph(line)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


async def generate_and_save_reports(
    task_id: str,
    session_factory: Optional[async_sessionmaker] = None,
) -> tuple[str, str]:
    factory = session_factory or database.SessionLocal
    async with factory() as session:
        result = await session.execute(
            select(DiagnosisResult)
            .where(DiagnosisResult.task_id == task_id)
            .order_by(DiagnosisResult.sort_order)
        )
        rows = list(result.scalars().all())

    results: list[dict[str, Any]] = [
        {
            "content_title": row.content_title,
            "description": row.description,
            "result": row.result,
            "evidence": row.evidence,
            "suggestion": row.suggestion,
        }
        for row in rows
    ]

    out_dir = Path(REPORT_DIR) / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    docx_path = out_dir / "report.docx"

    markdown = build_markdown(task_id, results)
    md_path.write_text(markdown, encoding="utf-8")
    write_docx(str(docx_path), markdown)

    return str(md_path), str(docx_path)
