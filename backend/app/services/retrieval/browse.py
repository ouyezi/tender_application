from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IndexJob, KnowledgeChunk, KnowledgeTag, WikiPage, WorkspaceFile
from app.services.retrieval.fts import search_fts
from app.services.retrieval.persist import load_chunk_text
from app.services.retrieval.provider import _task_index_status

TEXT_PREVIEW_LIMIT = 4000
_FTS_BROWSE_LIMIT = 5000


def _dt_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _chunk_matches_tag(chunk: KnowledgeChunk, tag_name: str) -> bool:
    for tag in _parse_json_list(chunk.tags):
        if isinstance(tag, dict) and tag.get("name") == tag_name:
            return True
        if tag == tag_name:
            return True
    return False


def _chunk_matches_node(chunk: KnowledgeChunk, node_id: str) -> bool:
    if chunk.node_id == node_id:
        return True
    return node_id in _parse_json_list(chunk.ancestor_node_ids)


def _chunk_to_list_item(chunk: KnowledgeChunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "file_id": chunk.file_id,
        "node_id": chunk.node_id,
        "parent_node_id": chunk.parent_node_id,
        "segment_level": chunk.segment_level,
        "title": chunk.title,
        "summary": chunk.summary,
        "description": chunk.description,
        "tags": _parse_json_list(chunk.tags),
        "title_path": _parse_json_list(chunk.title_path),
        "source": chunk.source,
        "index_status": chunk.index_status,
        "embedding_status": chunk.embedding_status,
        "start": chunk.start,
        "end": chunk.end,
        "child_chunk_ids": _parse_json_list(chunk.child_chunk_ids),
    }


def _chunk_to_detail(chunk: KnowledgeChunk) -> dict[str, Any]:
    text = load_chunk_text(chunk)
    truncated = False
    if len(text) > TEXT_PREVIEW_LIMIT:
        text = text[:TEXT_PREVIEW_LIMIT]
        truncated = True
    item = _chunk_to_list_item(chunk)
    item["text"] = text
    item["text_truncated"] = truncated
    item["ancestor_node_ids"] = _parse_json_list(chunk.ancestor_node_ids)
    return item


async def list_chunks(
    session: AsyncSession,
    *,
    task_id: str,
    q: str | None = None,
    file_id: str | None = None,
    segment_level: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    index_status: str | None = None,
    embedding_status: str | None = None,
    node_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))
    search_degraded = False

    stmt = select(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id)
    if file_id:
        stmt = stmt.where(KnowledgeChunk.file_id == file_id)
    if segment_level:
        stmt = stmt.where(KnowledgeChunk.segment_level == segment_level)
    if source:
        stmt = stmt.where(KnowledgeChunk.source == source)
    if index_status:
        stmt = stmt.where(KnowledgeChunk.index_status == index_status)
    if embedding_status:
        stmt = stmt.where(KnowledgeChunk.embedding_status == embedding_status)

    q_text = (q or "").strip()
    if q_text:
        fts_ids: list[str] | None = None
        try:
            hits = await search_fts(
                session, task_id, q_text, limit=_FTS_BROWSE_LIMIT
            )
            fts_ids = [str(hit["chunk_id"]) for hit in hits]
        except Exception:
            search_degraded = True
            fts_ids = None

        if fts_ids is not None:
            if not fts_ids:
                return {
                    "items": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "search_degraded": False,
                }
            stmt = stmt.where(KnowledgeChunk.chunk_id.in_(fts_ids))
        else:
            pattern = f"%{q_text}%"
            stmt = stmt.where(
                or_(
                    KnowledgeChunk.title.like(pattern),
                    KnowledgeChunk.summary.like(pattern),
                    KnowledgeChunk.description.like(pattern),
                )
            )

    stmt = stmt.order_by(KnowledgeChunk.id.asc())
    result = await session.execute(stmt)
    chunks = list(result.scalars().all())

    if node_id:
        chunks = [c for c in chunks if _chunk_matches_node(c, node_id)]
    if tag:
        chunks = [c for c in chunks if _chunk_matches_tag(c, tag)]

    total = len(chunks)
    start = (page - 1) * page_size
    page_chunks = chunks[start : start + page_size]
    items = [_chunk_to_list_item(c) for c in page_chunks]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "search_degraded": search_degraded,
    }


async def get_chunk(
    session: AsyncSession,
    *,
    task_id: str,
    chunk_id: str,
) -> dict[str, Any] | None:
    result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.chunk_id == chunk_id,
        )
    )
    chunk = result.scalar_one_or_none()
    if chunk is None:
        return None
    return _chunk_to_detail(chunk)


async def list_tags(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        select(KnowledgeTag)
        .where(KnowledgeTag.enabled == 1)
        .order_by(KnowledgeTag.name.asc())
    )
    tags: list[dict[str, Any]] = []
    for row in result.scalars().all():
        tags.append(
            {
                "name": row.name,
                "aliases": _parse_json_list(row.aliases),
                "description": row.description or "",
            }
        )
    return tags


def _wiki_to_list_item(page: WikiPage) -> dict[str, Any]:
    return {
        "wiki_id": page.id,
        "title": page.title,
        "summary": page.summary,
        "description": page.description,
        "tags": _parse_json_list(page.tags),
        "member_chunk_ids": _parse_json_list(page.member_chunk_ids),
    }


async def list_wiki_pages(
    session: AsyncSession,
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(WikiPage)
        .where(WikiPage.task_id == task_id)
        .order_by(WikiPage.id.asc())
    )
    return [_wiki_to_list_item(page) for page in result.scalars().all()]


async def get_wiki_page(
    session: AsyncSession,
    *,
    task_id: str,
    wiki_id: int,
) -> dict[str, Any] | None:
    result = await session.execute(
        select(WikiPage).where(
            WikiPage.task_id == task_id,
            WikiPage.id == wiki_id,
        )
    )
    page = result.scalar_one_or_none()
    if page is None:
        return None

    item = _wiki_to_list_item(page)
    member_ids = item["member_chunk_ids"]
    member_summaries: list[dict[str, Any]] = []
    if member_ids:
        chunk_result = await session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.task_id == task_id,
                KnowledgeChunk.chunk_id.in_(member_ids),
            )
        )
        by_id = {c.chunk_id: c for c in chunk_result.scalars().all()}
        for chunk_id in member_ids:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            member_summaries.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "title": chunk.title,
                    "summary": chunk.summary,
                }
            )
    item["member_summaries"] = member_summaries
    return item


async def get_index_status(
    session: AsyncSession,
    *,
    task_id: str,
) -> dict[str, Any]:
    index_status, incomplete = await _task_index_status(session, task_id)

    chunk_result = await session.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id)
    )
    chunks = list(chunk_result.scalars().all())
    fine = sum(1 for c in chunks if c.segment_level == "fine")
    large = sum(1 for c in chunks if c.segment_level == "large")
    ready_embeddings = sum(1 for c in chunks if c.embedding_status == "ready")
    total = len(chunks)
    embedding_ready_ratio = (ready_embeddings / total) if total else 0.0

    job_result = await session.execute(
        select(IndexJob)
        .where(IndexJob.task_id == task_id)
        .order_by(IndexJob.id.asc())
    )
    jobs = list(job_result.scalars().all())

    labels: dict[str, str] = {}
    file_ids = {job.file_id for job in jobs}
    if file_ids:
        wf_result = await session.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.task_id == task_id,
                WorkspaceFile.id.in_(file_ids),
            )
        )
        for wf in wf_result.scalars().all():
            labels[wf.id] = wf.label

    files = [
        {
            "file_id": job.file_id,
            "label": labels.get(job.file_id),
            "status": job.status,
            "stage": job.stage,
            "progress_done": job.progress_done,
            "progress_total": job.progress_total,
            "error_message": job.error_message,
            "created_at": _dt_iso(job.created_at),
            "started_at": _dt_iso(job.started_at),
            "finished_at": _dt_iso(job.finished_at),
        }
        for job in jobs
    ]

    fts_available = False
    try:
        await search_fts(session, task_id, "测", limit=1)
        fts_available = True
    except Exception:
        fts_available = any(
            c.segment_level == "fine" and c.index_status == "ready" for c in chunks
        )

    return {
        "index_status": index_status,
        "incomplete": incomplete,
        "counts": {"fine": fine, "large": large},
        "embedding_ready_ratio": embedding_ready_ratio,
        "files": files,
        "fts_available": fts_available,
    }
