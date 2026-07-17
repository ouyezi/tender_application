from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    INDEX_TAG_MIN_CONFIDENCE,
    PRECISE_SEARCH_CHANNEL_WEIGHT_FTS,
    PRECISE_SEARCH_CHANNEL_WEIGHT_VECTOR,
    PRECISE_SEARCH_CHANNEL_WEIGHT_WIKI,
    PRECISE_SEARCH_RECALL_PER_CHANNEL,
    PRECISE_SEARCH_TOP_K,
    UPLOAD_DIR,
)
from app.engine.base import RetrievalHit, RetrievalResult
from app.models import DiagnosisTask, IndexJob, KnowledgeChunk, WorkspaceFile
from app.services.retrieval.fts import search_fts
from app.services.retrieval.persist import load_chunk_text
from app.services.retrieval.rerank import AiReranker, MockAiReranker
from app.services.retrieval.rewrite import MockQueryRewriter, QueryRewriter
from app.services.retrieval.tags import load_tag_catalog, validate_target_tags
from app.services.retrieval.vectors import VectorIndex, get_embedding_model
from app.services.retrieval.wiki import search_wiki


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


def _get_query_rewriter() -> QueryRewriter:
    return MockQueryRewriter()


def _get_ai_reranker() -> AiReranker:
    return MockAiReranker()


async def ai_rerank_hits(
    session: AsyncSession,
    requirement: str,
    hits: list[RetrievalHit],
) -> list[str]:
    del session
    reranker = _get_ai_reranker()
    return await reranker.rerank(requirement, hits)


def _normalize_bm25_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    max_score = max(abs(score) for score in scores) or 1.0
    return [abs(score) / max_score for score in scores]


def _merge_channel_scores(
    vector_hits: list[tuple[str, float]],
    fts_hits: list[dict[str, object]],
    wiki_hits: list[tuple[str, float]],
) -> dict[str, float]:
    merged: dict[str, float] = {}

    for chunk_id, score in vector_hits:
        merged[chunk_id] = merged.get(chunk_id, 0.0) + (
            PRECISE_SEARCH_CHANNEL_WEIGHT_VECTOR * score
        )

    bm25_norm = _normalize_bm25_scores(
        [float(hit["score"]) for hit in fts_hits]
    )
    for hit, norm in zip(fts_hits, bm25_norm):
        chunk_id = str(hit["chunk_id"])
        merged[chunk_id] = merged.get(chunk_id, 0.0) + (
            PRECISE_SEARCH_CHANNEL_WEIGHT_FTS * norm
        )

    for chunk_id, boost in wiki_hits:
        merged[chunk_id] = merged.get(chunk_id, 0.0) + (
            PRECISE_SEARCH_CHANNEL_WEIGHT_WIKI * boost
        )

    return merged


async def _search_vector_channel(
    session: AsyncSession,
    task_id: str,
    vector_query: str,
    *,
    limit: int,
) -> list[tuple[str, float]]:
    result = await session.execute(
        select(KnowledgeChunk.file_id)
        .where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.segment_level == "fine",
            KnowledgeChunk.embedding_status == "ready",
        )
        .distinct()
    )
    file_ids = [row[0] for row in result.all()]
    if not file_ids:
        return []

    model = get_embedding_model()
    query_vec = model.embed(vector_query)
    hits: list[tuple[str, float]] = []

    for file_id in file_ids:
        index = VectorIndex(UPLOAD_DIR / task_id / "vectors" / file_id)
        hits.extend(index.search(query_vec, top_k=limit))

    hits.sort(key=lambda item: item[1], reverse=True)
    return hits[:limit]


async def _precise_search(
    session: AsyncSession,
    task_id: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None,
    index_status: str,
    incomplete: bool,
    *,
    ai_rerank=None,
) -> RetrievalResult:
    query = str(content_target.get("query") or "").strip()
    hints = list((item_hints or {}).get("retrieval_hints") or [])
    if not query and not hints:
        return RetrievalResult(
            mode="precise_search",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error="missing query",
        )

    degraded = False
    rewrite = {
        "vector_query": query or " ".join(hints),
        "keywords": hints or ([query] if query else []),
        "wiki_query": query or " ".join(hints),
    }
    try:
        rewrite = await _get_query_rewriter().rewrite(query, hints)
    except Exception:
        degraded = True

    vector_query = str(rewrite.get("vector_query") or query)
    keywords = [str(k) for k in rewrite.get("keywords") or [] if str(k).strip()]
    wiki_query = str(rewrite.get("wiki_query") or query)
    fts_query = " ".join(keywords) if keywords else vector_query

    recall_limit = PRECISE_SEARCH_RECALL_PER_CHANNEL
    vector_hits = await _search_vector_channel(
        session, task_id, vector_query, limit=recall_limit
    )
    fts_hits = await search_fts(
        session, task_id, fts_query, limit=recall_limit
    )
    wiki_hits = await search_wiki(
        session, task_id, wiki_query, limit=recall_limit
    )

    merged_scores = _merge_channel_scores(vector_hits, fts_hits, wiki_hits)
    if not merged_scores:
        return RetrievalResult(
            mode="precise_search",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            degraded=degraded,
        )

    chunk_result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.chunk_id.in_(list(merged_scores.keys())),
        )
    )
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunk_result.scalars().all()}

    ranked_ids = sorted(
        merged_scores,
        key=lambda chunk_id: merged_scores[chunk_id],
        reverse=True,
    )[:PRECISE_SEARCH_TOP_K]

    candidate_hits: list[RetrievalHit] = []
    for chunk_id in ranked_ids:
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        hit = _chunk_to_hit(chunk)
        hit.score = merged_scores[chunk_id]
        candidate_hits.append(hit)

    rerank_fn = ai_rerank or ai_rerank_hits
    try:
        reranked_ids = await rerank_fn(
            session,
            query or fts_query,
            candidate_hits,
        )
    except Exception:
        degraded = True
        reranked_ids = [hit.chunk_id for hit in candidate_hits]

    order = {chunk_id: idx for idx, chunk_id in enumerate(reranked_ids)}
    candidate_hits.sort(
        key=lambda hit: (order.get(hit.chunk_id, len(order)), -hit.score)
    )

    fine_chunks = [
        chunk_by_id[hit.chunk_id]
        for hit in candidate_hits
        if hit.chunk_id in chunk_by_id
        and chunk_by_id[hit.chunk_id].segment_level == "fine"
    ]
    all_chunks_result = await session.execute(
        select(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id)
    )
    all_chunks = all_chunks_result.scalars().all()
    large_by_node = {
        chunk.node_id: chunk
        for chunk in all_chunks
        if chunk.segment_level == "large"
    }

    fine_to_expanded: dict[str, KnowledgeChunk] = {}
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
        fine_to_expanded[fine.chunk_id] = expanded or fine

    final_hits: list[RetrievalHit] = []
    seen: set[str] = set()
    for hit in candidate_hits:
        chunk = chunk_by_id.get(hit.chunk_id)
        if chunk is None:
            continue
        if chunk.segment_level == "fine":
            chunk = fine_to_expanded.get(chunk.chunk_id, chunk)
        if chunk.chunk_id in seen:
            continue
        seen.add(chunk.chunk_id)
        final_hit = _chunk_to_hit(chunk)
        final_hit.score = hit.score
        final_hits.append(final_hit)

    return RetrievalResult(
        mode="precise_search",
        items=final_hits,
        index_status=index_status,
        incomplete=incomplete,
        degraded=degraded,
    )


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
    ai_rerank=None,
) -> RetrievalResult:
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
        return await _precise_search(
            session,
            task_id,
            content_target,
            item_hints,
            index_status,
            incomplete,
            ai_rerank=ai_rerank,
        )

    return RetrievalResult(
        mode=content_source,
        items=[],
        index_status=index_status,
        incomplete=incomplete,
        error="unknown content_source",
    )
