"""Background worker that drains queued ``ParseJob`` rows one at a time.

Mirrors the shape of ``app.services.scheduler`` but is much simpler: there is
no pause/resume/stop lifecycle, just a single loop that claims the oldest
queued job, runs the parse pipeline for it, persists the outcome onto the
``ParseJob``/``WorkspaceFile`` rows, and refreshes the workspace index.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from sqlalchemy import select

from app import db as database
from app.models import ParseJob, WorkspaceFile, utcnow
from app.services import workspace
from app.services.parse.pipeline import run_parse_pipeline

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 30.0

# Fatal pipeline errors are prefixed with the stage that produced them; use
# that prefix to record which stage the job actually failed at.
_FATAL_STAGE_BY_ERROR_PREFIX = (
    ("unsupported_extension", "convert"),
    ("convert_failed", "convert"),
    ("build_tree_failed", "build_tree"),
    ("chunk_failed", "chunk"),
)

_worker: Optional[asyncio.Task] = None
_wake: Optional[asyncio.Event] = None


def _get_wake() -> asyncio.Event:
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    return _wake


def _stage_for_error(error: Optional[str]) -> str:
    if error:
        for prefix, stage in _FATAL_STAGE_BY_ERROR_PREFIX:
            if error.startswith(prefix):
                return stage
    return "write_index"


async def kick() -> None:
    """Ensure the background worker is running, then wake it up."""
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_loop())
    _get_wake().set()


async def reset_for_tests() -> None:
    """Clear in-memory scheduler state between tests.

    Awaits the cancelled worker task (instead of merely calling ``cancel()``)
    so it fully unwinds — including any in-flight DB session — before the
    test's event loop is torn down. Leaving a cancelled-but-not-yet-finished
    task dangling can otherwise deadlock ``asyncio.run()``'s teardown when it
    tries to cancel/await the same task itself.
    """
    global _worker, _wake
    worker = _worker
    _worker = None
    _wake = None
    if worker is not None and not worker.done():
        worker.cancel()
        try:
            await worker
        except (asyncio.CancelledError, Exception):
            pass


async def _loop() -> None:
    wake = _get_wake()
    while True:
        job_id = await _claim_next_queued()
        if job_id is None:
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=IDLE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue
        try:
            await _run_job(job_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("parse_scheduler: unhandled error running job %s", job_id)


async def _claim_next_queued() -> Optional[int]:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(ParseJob)
            .where(ParseJob.status == "queued")
            .order_by(ParseJob.created_at.asc(), ParseJob.id.asc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.stage = "convert"
        job.started_at = utcnow()
        await session.commit()
        return job.id


async def _run_job(job_id: int) -> None:
    async with database.SessionLocal() as session:
        job = await session.get(ParseJob, job_id)
        if job is None:
            return
        file_id = job.file_id
        task_id = job.task_id
        wf = await session.get(WorkspaceFile, file_id)
        if wf is None:
            job.status = "failed"
            job.error_message = "workspace_file_not_found"
            job.finished_at = utcnow()
            await session.commit()
            return
        stored_path = wf.stored_path
        wf.parse_status = "running"
        wf.updated_at = utcnow()
        await session.commit()

    try:
        result = await run_parse_pipeline(file_id, task_id, stored_path)
    except Exception as exc:
        async with database.SessionLocal() as session:
            job = await session.get(ParseJob, job_id)
            wf = await session.get(WorkspaceFile, file_id)
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)
                job.finished_at = utcnow()
            if wf is not None:
                wf.parse_status = "failed"
                wf.parse_error = str(exc)
                wf.updated_at = utcnow()
            await session.commit()
            await workspace.artifact_refresh_index(session, task_id)
        return

    async with database.SessionLocal() as session:
        job = await session.get(ParseJob, job_id)
        wf = await session.get(WorkspaceFile, file_id)
        if wf is not None:
            wf.parse_status = result["status"]
            # Surface non-fatal warnings on partial so the UI can explain the badge.
            err = result.get("error")
            warnings = result.get("warnings") or []
            if not err and result["status"] == "partial" and warnings:
                err = "; ".join(warnings)
            wf.parse_error = err
            wf.md_path = result.get("md_path")
            wf.tree_path = result.get("tree_path")
            wf.chunks_path = result.get("chunks_path")
            wf.updated_at = utcnow()
        if job is not None:
            job.status = "succeeded" if result["status"] in ("succeeded", "partial") else "failed"
            job.stage = _stage_for_error(result.get("error"))
            job.error_message = result.get("error")
            job.warnings = json.dumps(result.get("warnings") or [], ensure_ascii=False)
            job.finished_at = utcnow()
        await session.commit()
        await workspace.artifact_refresh_index(session, task_id)
