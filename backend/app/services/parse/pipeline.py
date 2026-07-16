"""Orchestrate the per-file parse pipeline: convert → extract → build_tree → chunk → write meta.

Stateless with respect to the DB: callers (the parse scheduler / Workspace
API — out of scope here) are responsible for persisting ``WorkspaceFile``
status/paths and refreshing ``index.md`` using the returned dict. See
docs/superpowers/specs/2026-07-16-workspace-management-design.md §5.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.services import artifact
from app.services.parse import chunk as chunk_mod
from app.services.parse import convert as convert_mod
from app.services.parse import extract as extract_mod
from app.services.parse.tree import build_document_tree, flatten_nodes

SUPPORTED_EXTENSIONS = {".docx", ".pdf"}


def _result(
    status: str,
    *,
    md_path: Path | None = None,
    tree_path: Path | None = None,
    chunks_path: Path | None = None,
    error: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "md_path": str(md_path) if md_path else None,
        "tree_path": str(tree_path) if tree_path else None,
        "chunks_path": str(chunks_path) if chunks_path else None,
        "error": error,
        "warnings": warnings or [],
    }


async def run_parse_pipeline(file_id: str, task_id: str, stored_path: str) -> dict[str, Any]:
    """Run the full parse pipeline for one file, writing all artifacts to disk.

    Returns ``{"status": "succeeded"|"partial"|"failed", "md_path", "tree_path",
    "chunks_path", "error", "warnings"}``. Convert / build_tree failures are
    fatal (``failed``); table extraction failures are non-fatal (``partial``
    + warnings); a tree with no headings alone still yields ``succeeded``
    (with a ``no_headings`` warning).
    """
    warnings: list[str] = []
    root = artifact.ensure_artifact_dirs(task_id)

    src_path = Path(stored_path)
    ext = src_path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return _result("failed", error=f"unsupported_extension:{ext}")

    md_dir = root / "markdown"
    json_dir = root / "json"
    image_dir = root / "image" / file_id
    table_dir = root / "table" / file_id

    md_path = md_dir / f"{file_id}.md"
    tree_path = json_dir / f"{file_id}.tree.json"
    chunks_path = json_dir / f"{file_id}.chunks.json"
    meta_path = json_dir / f"{file_id}.meta.json"

    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    # --- convert ---------------------------------------------------------
    try:
        if ext == ".docx":
            markdown = await asyncio.to_thread(convert_mod.convert_docx_to_markdown, src_path, image_dir)
        else:
            markdown = await asyncio.to_thread(convert_mod.convert_pdf_to_markdown, src_path, image_dir)
    except Exception as exc:
        return _result("failed", error=f"convert_failed: {exc}")

    # --- extract (tables; image links normalized after) -------------------
    table_warnings: list[str] = []
    try:
        if ext == ".docx":
            _table_ids, table_warnings = await asyncio.to_thread(
                extract_mod.extract_tables_from_docx, src_path, table_dir
            )
        else:
            _table_ids, table_warnings = await asyncio.to_thread(
                extract_mod.extract_tables_from_pdf, src_path, table_dir
            )
    except Exception as exc:
        _table_ids = []
        table_warnings = [f"extract_failed: {exc}"]
    warnings.extend(table_warnings)

    markdown = extract_mod.normalize_image_paths(markdown, file_id)
    await asyncio.to_thread(md_path.write_text, markdown, encoding="utf-8")

    # --- build_tree --------------------------------------------------------
    try:
        tree = await asyncio.to_thread(build_document_tree, markdown)
    except Exception as exc:
        return _result("failed", md_path=md_path, error=f"build_tree_failed: {exc}", warnings=warnings)
    warnings.extend(tree.get("warnings", []))

    await asyncio.to_thread(
        tree_path.write_text, json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- chunk ---------------------------------------------------------------
    try:
        chunks = await asyncio.to_thread(chunk_mod.chunk_from_tree, markdown, tree)
    except Exception as exc:
        return _result(
            "failed", md_path=md_path, tree_path=tree_path, error=f"chunk_failed: {exc}", warnings=warnings
        )

    await asyncio.to_thread(
        chunks_path.write_text, json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # --- write meta ------------------------------------------------------
    status = "partial" if table_warnings else "succeeded"
    meta = {
        "file_id": file_id,
        "task_id": task_id,
        "status": status,
        "table_count": len(_table_ids),
        "node_count": len(flatten_nodes(tree)),
        "chunk_count": len(chunks),
        "warnings": warnings,
    }
    await asyncio.to_thread(
        meta_path.write_text, json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return _result(
        status,
        md_path=md_path,
        tree_path=tree_path,
        chunks_path=chunks_path,
        warnings=warnings,
    )
