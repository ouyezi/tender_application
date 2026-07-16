from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import UPLOAD_DIR  # re-export for tests monkeypatch if needed
from app.models import DiagnosisTask, ParseJob, WorkspaceFile, utcnow
from app.services import artifact


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
    from sqlalchemy import select

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
