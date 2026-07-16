from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from app.config import UPLOAD_DIR

ARTIFACT_SUBDIRS = ("document", "markdown", "image", "table", "json", "report", "other")


def artifact_root(task_id: str) -> Path:
    return UPLOAD_DIR / task_id


def ensure_artifact_dirs(task_id: str) -> Path:
    root = artifact_root(task_id)
    root.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACT_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _safe_name(name: str) -> str:
    base = Path(name).name
    return re.sub(r"[^\w.\u4e00-\u9fff\-]+", "_", base)[:180] or "file"


def move_into_document(task_id: str, src: Path, *, file_id: str, original_name: str) -> Path:
    root = ensure_artifact_dirs(task_id)
    ext = Path(original_name).suffix.lower() or src.suffix.lower()
    dest = root / "document" / f"{file_id}_{_safe_name(original_name)}"
    if dest.suffix.lower() != ext and ext:
        dest = dest.with_suffix(ext)
    src = Path(src)
    if src.resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dest)
    return dest


def write_index_md(task_id: str, files: Iterable[dict[str, Any]]) -> Path:
    root = ensure_artifact_dirs(task_id)
    lines = [
        f"# Workspace Index — {task_id}",
        "",
        "| file_id | label | filename | kind | status | markdown | tree |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in files:
        lines.append(
            "| {file_id} | {label} | {original_filename} | {kind} | {parse_status} | {md_path} | {tree_path} |".format(
                file_id=f.get("file_id", ""),
                label=f.get("label", ""),
                original_filename=f.get("original_filename", ""),
                kind=f.get("kind", ""),
                parse_status=f.get("parse_status", ""),
                md_path=f.get("md_path") or "",
                tree_path=f.get("tree_path") or "",
            )
        )
        if f.get("warnings"):
            lines.append(f"")
            lines.append(f"- warnings ({f['file_id']}): {f['warnings']}")
    path = root / "index.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
