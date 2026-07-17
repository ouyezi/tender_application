"""Background worker that drains queued ``IndexJob`` rows one at a time.

Mirrors ``app.services.parse_scheduler``: claim the oldest queued job,
materialize fine/large segments from parse artifacts, enrich them with
controlled tags via ``MockChunkEnricher``, persist as ``KnowledgeChunk`` rows,
and mark the job ``ready``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app import config
from app import db as database
from app.models import IndexJob, KnowledgeChunk, WorkspaceFile, utcnow
from app.services.retrieval.enricher import MockChunkEnricher
from app.services.retrieval.fts import rebuild_fts_for_file
from app.services.retrieval.persist import (
    external_text_paths,
    invalidate_file_index,
    load_chunk_text,
    purge_orphaned_text_files,
    write_segments,
)
from app.services.retrieval.vectors import VectorIndex, get_embedding_model
from app.services.retrieval.wiki import MockWikiBuilder
from app.services.retrieval.segments import materialize_segments
from app.services.retrieval.tags import load_tag_catalog

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 30.0

_worker: Optional[asyncio.Task] = None
_wake: Optional[asyncio.Event] = None


def _get_wake() -> asyncio.Event:
    global _wake
    if _wake is None:
        _wake = asyncio.Event()
    return _wake


async def enqueue(task_id: str, file_id: str) -> None:
    async with database.SessionLocal() as session:
        session.add(
            IndexJob(
                task_id=task_id,
                file_id=file_id,
                status="queued",
                stage="segments",
            )
        )
        await session.commit()


async def kick() -> None:
    """Ensure the background worker is running, then wake it up."""
    global _worker
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_loop())
    _get_wake().set()


async def reset_for_tests() -> None:
    """Clear in-memory scheduler state between tests."""
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


async def drain_once_for_tests() -> None:
    """Process a single queued job synchronously (for unit tests)."""
    job_id = await _claim_next_queued()
    if job_id is not None:
        await _run_job(job_id)


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
            logger.exception("index_scheduler: unhandled error running job %s", job_id)


async def _rebuild_vectors_for_file(
    session,
    task_id: str,
    file_id: str,
) -> None:
    """Embed fine chunks and persist a per-file vector index."""
    result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.file_id == file_id,
            KnowledgeChunk.segment_level == "fine",
        )
    )
    fine_chunks = result.scalars().all()
    model = get_embedding_model()
    vector_path = config.UPLOAD_DIR / task_id / "vectors" / file_id
    index = VectorIndex(vector_path)

    if not fine_chunks:
        index.upsert([])
        return

    texts = [
        f"{chunk.title} {load_chunk_text(chunk)}".strip()
        for chunk in fine_chunks
    ]
    vectors = model.embed_many(texts)
    index.upsert(
        [
            (chunk.chunk_id, vector)
            for chunk, vector in zip(fine_chunks, vectors)
        ]
    )
    for chunk in fine_chunks:
        chunk.embedding_status = "ready"


async def _rebuild_wiki_for_task(session, task_id: str) -> None:
    """Aggregate fine chunks by tag into task-level wiki pages."""
    await MockWikiBuilder().build_for_task(session, task_id)


async def _claim_next_queued() -> Optional[int]:
    async with database.SessionLocal() as session:
        result = await session.execute(
            select(IndexJob)
            .where(IndexJob.status == "queued")
            .order_by(IndexJob.created_at.asc(), IndexJob.id.asc())
            .limit(1)
        )
        job = result.scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.stage = "segments"
        job.started_at = utcnow()
        await session.commit()
        return job.id


async def _run_job(job_id: int) -> None:
    async with database.SessionLocal() as session:
        job = await session.get(IndexJob, job_id)
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

        md_path = wf.md_path
        tree_path = wf.tree_path
        chunks_path = wf.chunks_path

    if not md_path or not tree_path or not chunks_path:
        async with database.SessionLocal() as session:
            job = await session.get(IndexJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_message = "parse_artifacts_missing"
                job.finished_at = utcnow()
                await session.commit()
        return

    try:
        markdown = Path(md_path).read_text(encoding="utf-8")
        tree = json.loads(Path(tree_path).read_text(encoding="utf-8"))
        fine_chunks = json.loads(Path(chunks_path).read_text(encoding="utf-8"))
        segments = materialize_segments(markdown, tree, fine_chunks)
        text_dir = config.UPLOAD_DIR / task_id / "index_text"

        new_text_paths = external_text_paths(segments, text_dir)
        async with database.SessionLocal() as session:
            catalog = await load_tag_catalog(session)
            segments = await MockChunkEnricher().enrich_many(
                task_id=task_id,
                segments=segments,
                catalog=catalog,
            )
            old_text_paths = await invalidate_file_index(session, task_id, file_id)
            await write_segments(session, task_id, file_id, segments, text_dir)
            await rebuild_fts_for_file(session, task_id, file_id)
            await _rebuild_vectors_for_file(session, task_id, file_id)
            await _rebuild_wiki_for_task(session, task_id)
            job = await session.get(IndexJob, job_id)
            if job is not None:
                job.status = "ready"
                job.stage = "enrich"
                job.progress_done = len(segments)
                job.progress_total = len(segments)
                job.finished_at = utcnow()
            await session.commit()
        purge_orphaned_text_files(old_text_paths, new_text_paths)
    except Exception as exc:
        async with database.SessionLocal() as session:
            job = await session.get(IndexJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)
                job.finished_at = utcnow()
                await session.commit()
