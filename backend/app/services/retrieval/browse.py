from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeChunk, KnowledgeTag
from app.services.retrieval.fts import search_fts
from app.services.retrieval.persist import load_chunk_text

TEXT_PREVIEW_LIMIT = 4000
_FTS_BROWSE_LIMIT = 5000


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
