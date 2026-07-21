from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR  # re-export for tests monkeypatch if needed
from app.models import DiagnosisTask, ParseJob, WorkspaceFile, utcnow
from app.services import artifact

_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


class ReparseConflict(Exception):
    """Raised when a reparse is requested while a job is already pending/running."""


def new_file_id() -> str:
    return uuid.uuid4().hex[:12]


async def enqueue_parse(session: AsyncSession, wf: WorkspaceFile, *, attempt: int = 1) -> ParseJob:
    job = ParseJob(
        file_id=wf.id,
        task_id=wf.task_id,
        status="queued",
        stage="convert",
        attempt=attempt,
    )
    wf.parse_status = "pending"
    wf.updated_at = utcnow()
    session.add(job)
    await session.flush()
    return job


async def register_task_documents(
    session: AsyncSession,
    *,
    task_id: str,
    tender_path: str,
    tender_filename: str,
    bid_path: str,
    bid_filename: str,
    enqueue_parse: bool = True,
) -> tuple[WorkspaceFile, WorkspaceFile]:
    artifact.ensure_artifact_dirs(task_id)
    pairs = [
        ("招标文件", tender_path, tender_filename, "tender"),
        ("标书", bid_path, bid_filename, "bid"),
    ]
    created: list[WorkspaceFile] = []
    task = await session.get(DiagnosisTask, task_id)
    for label, path, filename, role in pairs:
        fid = new_file_id()
        dest = artifact.move_into_document(
            task_id, Path(path), file_id=fid, original_name=filename
        )
        wf = WorkspaceFile(
            id=fid,
            task_id=task_id,
            label=label,
            original_filename=filename,
            stored_path=str(dest),
            kind="document",
            ext=dest.suffix.lower(),
            parse_status="pending",
        )
        session.add(wf)
        await session.flush()
        if enqueue_parse:
            await enqueue_parse(session, wf)
        created.append(wf)
        if task is not None:
            if role == "tender":
                task.tender_file_id = fid
                task.tender_path = str(dest)
            else:
                task.bid_file_id = fid
                task.bid_path = str(dest)
    await artifact_refresh_index(session, task_id)
    return created[0], created[1]


async def artifact_refresh_index(session: AsyncSession, task_id: str) -> None:
    rows = (
        await session.execute(select(WorkspaceFile).where(WorkspaceFile.task_id == task_id))
    ).scalars().all()
    artifact.write_index_md(
        task_id,
        [
            {
                "file_id": r.id,
                "label": r.label,
                "original_filename": r.original_filename,
                "kind": r.kind,
                "parse_status": r.parse_status,
                "md_path": r.md_path or "",
                "tree_path": r.tree_path or "",
                "warnings": r.parse_error or "",
            }
            for r in rows
        ],
    )


async def _stream_save_upload(upload_file: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await upload_file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "file too large")
            out.write(chunk)


async def import_file(
    session: AsyncSession,
    *,
    task_id: str,
    upload_file: UploadFile,
    label: str = "",
) -> WorkspaceFile:
    """Save an uploaded workspace file, classify it, and (for pdf/docx) enqueue a parse job.

    Raises ``LookupError`` if the task doesn't exist. Caller is responsible
    for committing the session and calling ``parse_scheduler.kick()`` when
    the returned file's ``kind`` is ``"document"``.
    """
    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        raise LookupError("task_not_found")
    if not upload_file.filename:
        raise HTTPException(400, "file required")

    ext = Path(upload_file.filename).suffix.lower()
    kind = "document" if ext in ALLOWED_EXTENSIONS else "other"
    fid = new_file_id()
    dest = artifact.dest_path_for(task_id, kind, file_id=fid, original_name=upload_file.filename)
    await _stream_save_upload(upload_file, dest)

    wf = WorkspaceFile(
        id=fid,
        task_id=task_id,
        label=label,
        original_filename=upload_file.filename,
        stored_path=str(dest),
        kind=kind,
        ext=ext,
        parse_status="pending" if kind == "document" else "skipped",
    )
    session.add(wf)
    await session.flush()
    if kind == "document":
        await enqueue_parse(session, wf)
    await artifact_refresh_index(session, task_id)
    return wf


async def reparse(session: AsyncSession, wf: WorkspaceFile) -> ParseJob:
    """Queue a new parse attempt for ``wf``.

    Raises ``ReparseConflict`` if a job is already pending/running, and
    ``ValueError`` if the file isn't a parseable document.
    """
    if wf.kind != "document":
        raise ValueError("not_a_document")
    if wf.parse_status in ("pending", "running"):
        raise ReparseConflict("parse already in progress")

    last_job = (
        await session.execute(
            select(ParseJob)
            .where(ParseJob.file_id == wf.id)
            .order_by(ParseJob.attempt.desc(), ParseJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    attempt = (last_job.attempt + 1) if last_job else 1
    job = await enqueue_parse(session, wf, attempt=attempt)
    wf.parse_error = None
    await artifact_refresh_index(session, wf.task_id)
    return job


def _normalize_tree_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for node in nodes:
        copy = dict(node)
        copy["numbering"] = node.get("numbering") or ""
        copy["children"] = _normalize_tree_nodes(node.get("children") or [])
        normalized.append(copy)
    return normalized


def get_tree(wf: WorkspaceFile) -> list[dict[str, Any]]:
    """Load and return the nested section tree for a parsed file."""
    if not wf.tree_path or not Path(wf.tree_path).is_file():
        raise FileNotFoundError("tree_not_found")
    data = json.loads(Path(wf.tree_path).read_text(encoding="utf-8"))
    return _normalize_tree_nodes(data.get("nodes", []))


_RELATIVE_ARTIFACT_LINK_RE = re.compile(
    r"(\]\()\.\./(image|table)/([^/]+)/([^)\s]+)(\))"
)


def _rewrite_artifact_links(markdown: str, task_id: str) -> str:
    """Rewrite ``../image|table/{file_id}/...`` links for browser HTTP access."""

    def repl(m: re.Match[str]) -> str:
        prefix, kind, file_id, name, suffix = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        return f"{prefix}/artifact-files/{task_id}/{kind}/{file_id}/{name}{suffix}"

    return _RELATIVE_ARTIFACT_LINK_RE.sub(repl, markdown)


def _find_node(nodes: list[dict[str, Any]], node_id: str) -> Optional[dict[str, Any]]:
    for node in nodes:
        if node.get("id") == node_id:
            return node
        found = _find_node(node.get("children") or [], node_id)
        if found is not None:
            return found
    return None


def get_content(wf: WorkspaceFile, node_id: str) -> dict[str, Any]:
    """Return markdown for a tree node, including descendant sections.

    Slice uses ``self_start:subtree_end`` so clicking a parent heading shows
    its own body plus all child chapters (not only the gap before the first
    child). Raises ``FileNotFoundError`` / ``LookupError`` when missing.
    """
    if not wf.tree_path or not Path(wf.tree_path).is_file():
        raise FileNotFoundError("tree_not_found")
    if not wf.md_path or not Path(wf.md_path).is_file():
        raise FileNotFoundError("markdown_not_found")

    data = json.loads(Path(wf.tree_path).read_text(encoding="utf-8"))
    node = _find_node(data.get("nodes", []), node_id)
    if node is None:
        raise LookupError("node_not_found")

    self_start = int(node.get("self_start", node.get("start_offset", 0)))
    subtree_end = int(node.get("subtree_end", node.get("end_offset", 0)))
    section_start = int(node.get("start_offset", self_start))
    section_end = int(node.get("end_offset", subtree_end))

    markdown = Path(wf.md_path).read_text(encoding="utf-8")
    section = markdown[self_start:subtree_end]
    section = _rewrite_artifact_links(section, wf.task_id)
    return {
        "node_id": node_id,
        "title": node.get("title", ""),
        "markdown": section,
        "start_offset": self_start,
        "end_offset": subtree_end,
        "self_start": self_start,
        "subtree_end": subtree_end,
        "section_start": section_start,
        "section_end": section_end,
    }


def _aggregate_counts(files: list[WorkspaceFile]) -> dict[str, int]:
    succeeded = sum(1 for f in files if f.parse_status in ("succeeded", "partial"))
    running = sum(1 for f in files if f.parse_status in ("pending", "running"))
    failed = sum(1 for f in files if f.parse_status == "failed")
    return {
        "file_count": len(files),
        "parse_succeeded": succeeded,
        "parse_running": running,
        "parse_failed": failed,
    }


async def list_workspaces(session: AsyncSession) -> list[dict[str, Any]]:
    tasks = (
        await session.execute(select(DiagnosisTask).order_by(DiagnosisTask.created_at.desc()))
    ).scalars().all()
    all_files = (await session.execute(select(WorkspaceFile))).scalars().all()
    files_by_task: dict[str, list[WorkspaceFile]] = {}
    for f in all_files:
        files_by_task.setdefault(f.task_id, []).append(f)

    items = []
    for task in tasks:
        files = files_by_task.get(task.id, [])
        item = {
            "task_id": task.id,
            "tender_filename": task.tender_filename,
            "bid_filename": task.bid_filename,
            "created_at": task.created_at,
        }
        item.update(_aggregate_counts(files))
        items.append(item)
    return items


def _enrich_parse_error_from_meta(wf: WorkspaceFile) -> None:
    """Fill empty parse_error for partial files from meta.json (legacy rows)."""
    if wf.parse_status != "partial" or wf.parse_error:
        return
    meta_path = artifact.artifact_root(wf.task_id) / "json" / f"{wf.id}.meta.json"
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    warnings = meta.get("warnings") or []
    if warnings:
        wf.parse_error = "; ".join(warnings)


async def get_workspace_detail(session: AsyncSession, task_id: str) -> Optional[dict[str, Any]]:
    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        return None
    files = (
        await session.execute(
            select(WorkspaceFile)
            .where(WorkspaceFile.task_id == task_id)
            .order_by(WorkspaceFile.created_at.asc())
        )
    ).scalars().all()
    for wf in files:
        _enrich_parse_error_from_meta(wf)
    return {
        "task_id": task.id,
        "tender_filename": task.tender_filename,
        "bid_filename": task.bid_filename,
        "files": files,
    }
