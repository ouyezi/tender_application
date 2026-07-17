from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INDEX_TAG_MIN_CONFIDENCE
from app.engine.base import RetrievalHit, RetrievalResult
from app.models import DiagnosisTask, IndexJob, KnowledgeChunk, WorkspaceFile
from app.services.retrieval.persist import load_chunk_text
from app.services.retrieval.tags import load_tag_catalog, validate_target_tags


async def _task_index_status(session: AsyncSession, task_id: str) -> tuple[str, bool]:
    """Return ``(index_status, incomplete)`` for a task's index jobs."""
    result = await session.execute(
        select(IndexJob).where(IndexJob.task_id == task_id)
    )
    jobs = result.scalars().all()
    if not jobs:
        return "unavailable", False

    statuses = {job.status for job in jobs}
    if statuses & {"partial", "running", "queued"}:
        if statuses & {"ready", "partial"}:
            return "partial", True
        return "unavailable", True
    if "ready" in statuses:
        return "ready", False
    if "failed" in statuses and not (statuses - {"failed"}):
        return "unavailable", False
    return "unavailable", False


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _chunk_to_hit(chunk: KnowledgeChunk, *, text: str | None = None) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk.chunk_id,
        file_id=chunk.file_id,
        node_id=chunk.node_id,
        segment_level=chunk.segment_level,
        title=chunk.title,
        summary=chunk.summary,
        title_path=_parse_json_list(chunk.title_path),
        tags=_parse_json_list(chunk.tags),
        text=text if text is not None else load_chunk_text(chunk),
        child_chunk_ids=_parse_json_list(chunk.child_chunk_ids),
    )


def _chunk_matches_tags(
    chunk: KnowledgeChunk,
    target_tags: set[str],
    min_confidence: float,
) -> bool:
    tags = _parse_json_list(chunk.tags)
    for tag in tags:
        name = tag.get("name")
        confidence = float(tag.get("confidence", 0.0))
        if name in target_tags and confidence >= min_confidence:
            return True
    return False


async def _resolve_file_role(
    session: AsyncSession,
    task_id: str,
    file_role: str,
) -> WorkspaceFile | None:
    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        return None

    file_id: str | None
    if file_role == "tender":
        file_id = task.tender_file_id
    elif file_role == "bid":
        file_id = task.bid_file_id
    else:
        return None

    if not file_id:
        return None
    return await session.get(WorkspaceFile, file_id)


async def _full_document(
    session: AsyncSession,
    task_id: str,
    content_target: dict[str, Any],
    index_status: str,
    incomplete: bool,
) -> RetrievalResult:
    file_role = str(content_target.get("file_role") or "tender")
    wf = await _resolve_file_role(session, task_id, file_role)
    if wf is None:
        return RetrievalResult(
            mode="full_document",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error=f"file_not_found:{file_role}",
        )
    if not wf.md_path or wf.parse_status not in {"succeeded", "partial"}:
        return RetrievalResult(
            mode="full_document",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error="markdown_unavailable",
        )

    text = Path(wf.md_path).read_text(encoding="utf-8")
    hit = RetrievalHit(
        chunk_id=f"full:{wf.id}",
        file_id=wf.id,
        node_id="",
        segment_level="document",
        title=wf.label or wf.original_filename,
        summary="",
        title_path=[wf.label or wf.original_filename],
        tags=[],
        text=text,
    )
    return RetrievalResult(
        mode="full_document",
        items=[hit],
        index_status=index_status,
        incomplete=incomplete,
    )


async def _expand_fine_to_large(
    session: AsyncSession,
    task_id: str,
    fine_chunks: list[KnowledgeChunk],
    large_by_node: dict[str, KnowledgeChunk],
) -> list[KnowledgeChunk]:
    """Replace fine hits with ancestor large segments when available."""
    out: list[KnowledgeChunk] = []
    seen: set[str] = set()

    for fine in fine_chunks:
        expanded: KnowledgeChunk | None = None
        for anc_id in _parse_json_list(fine.ancestor_node_ids):
            large = large_by_node.get(anc_id)
            if large is not None:
                expanded = large
                break
        if expanded is None:
            large = large_by_node.get(fine.node_id)
            if large is not None:
                expanded = large
        target = expanded or fine
        if target.chunk_id not in seen:
            out.append(target)
            seen.add(target.chunk_id)

    return out


async def _collection(
    session: AsyncSession,
    task_id: str,
    content_target: dict[str, Any],
    index_status: str,
    incomplete: bool,
) -> RetrievalResult:
    target_tags = list(content_target.get("target_tags") or [])
    if not target_tags:
        return RetrievalResult(
            mode="collection",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error="missing target_tags",
        )

    catalog = await load_tag_catalog(session)
    allowed = {row["name"] for row in catalog}
    ok, err = validate_target_tags(target_tags, allowed)
    if not ok:
        return RetrievalResult(
            mode="collection",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error=err,
        )

    target_set = set(target_tags)
    result = await session.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id)
    )
    all_chunks = result.scalars().all()

    matched = [
        c
        for c in all_chunks
        if c.index_status == "ready"
        and _chunk_matches_tags(c, target_set, INDEX_TAG_MIN_CONFIDENCE)
    ]

    large_by_node = {
        c.node_id: c for c in all_chunks if c.segment_level == "large"
    }

    fine_matched = [c for c in matched if c.segment_level == "fine"]
    large_matched = [c for c in matched if c.segment_level == "large"]

    expanded_fines = await _expand_fine_to_large(
        session, task_id, fine_matched, large_by_node
    )

    hits: list[RetrievalHit] = []
    seen: set[str] = set()
    for chunk in large_matched + expanded_fines:
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        hits.append(_chunk_to_hit(chunk))

    return RetrievalResult(
        mode="collection",
        items=hits,
        index_status=index_status,
        incomplete=incomplete,
    )


async def _large_segments(
    session: AsyncSession,
    task_id: str,
    content_target: dict[str, Any],
    index_status: str,
    incomplete: bool,
) -> RetrievalResult:
    file_role = str(content_target.get("file_role") or "bid")
    root_node_id = content_target.get("root_node_id")

    wf = await _resolve_file_role(session, task_id, file_role)
    if wf is None:
        return RetrievalResult(
            mode="large_segments",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error=f"file_not_found:{file_role}",
        )

    query = select(KnowledgeChunk).where(
        KnowledgeChunk.task_id == task_id,
        KnowledgeChunk.file_id == wf.id,
        KnowledgeChunk.segment_level == "large",
        KnowledgeChunk.index_status == "ready",
    )
    result = await session.execute(query)
    chunks = result.scalars().all()

    if root_node_id:
        root = str(root_node_id)
        chunks = [
            c
            for c in chunks
            if c.node_id == root
            or root in _parse_json_list(c.ancestor_node_ids)
        ]

    chunks.sort(key=lambda c: (c.start, c.chunk_id))
    hits = [_chunk_to_hit(c) for c in chunks]
    return RetrievalResult(
        mode="large_segments",
        items=hits,
        index_status=index_status,
        incomplete=incomplete,
    )


async def retrieve(
    session: AsyncSession,
    *,
    task_id: str,
    content_source: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None = None,
) -> RetrievalResult:
    del item_hints

    if not content_source:
        return RetrievalResult(
            mode="",
            items=[],
            index_status="unavailable",
            error="missing content_source",
        )

    index_status, incomplete = await _task_index_status(session, task_id)

    if content_source == "full_document":
        return await _full_document(
            session, task_id, content_target, index_status, incomplete
        )
    if content_source == "collection":
        return await _collection(
            session, task_id, content_target, index_status, incomplete
        )
    if content_source == "large_segments":
        return await _large_segments(
            session, task_id, content_target, index_status, incomplete
        )
    if content_source == "precise_search":
        return RetrievalResult(
            mode="precise_search",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error="not_implemented",
        )

    return RetrievalResult(
        mode=content_source,
        items=[],
        index_status=index_status,
        incomplete=incomplete,
        error="unknown content_source",
    )
