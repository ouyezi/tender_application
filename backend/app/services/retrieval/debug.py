from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import PRECISE_SEARCH_RECALL_PER_CHANNEL, PRECISE_SEARCH_TOP_K
from app.engine.base import RetrievalHit
from app.models import KnowledgeChunk
from app.services.retrieval.debug_types import DebugRetrievalResult, DebugTrace
from app.services.retrieval.fts import search_fts
from app.services.retrieval.provider import (
    _chunk_to_hit,
    _merge_channel_scores,
    _search_vector_channel,
    _task_index_status,
)
from app.services.retrieval.rerank import get_ai_reranker
from app.services.retrieval.rewrite import get_query_rewriter
from app.services.retrieval.wiki import search_wiki


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


async def retrieve_debug(
    session: AsyncSession,
    *,
    task_id: str,
    content_source: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None = None,
) -> DebugRetrievalResult:
    index_status, incomplete = await _task_index_status(session, task_id)

    if content_source != "precise_search":
        return DebugRetrievalResult(
            mode=content_source or "",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error="unsupported in task1",
        )

    return await _debug_precise_search(
        session,
        task_id=task_id,
        content_target=content_target,
        item_hints=item_hints,
        index_status=index_status,
        incomplete=incomplete,
    )


async def _debug_precise_search(
    session: AsyncSession,
    *,
    task_id: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None,
    index_status: str,
    incomplete: bool,
) -> DebugRetrievalResult:
    query = str(content_target.get("query") or "").strip()
    hints = [
        str(h).strip()
        for h in list((item_hints or {}).get("retrieval_hints") or [])
        if str(h).strip()
    ]
    if not query and hints:
        query = hints[0]
    if not query and not hints:
        return DebugRetrievalResult(
            mode="precise_search",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            error="missing query",
        )

    degraded = False
    rewrite_error: str | None = None
    try:
        rewrite = await get_query_rewriter().rewrite(query, hints)
    except Exception as exc:  # noqa: BLE001 — debug path degrades on AI failure
        degraded = True
        rewrite_error = f"rewrite failed: {exc}"
        rewrite = {
            "vector_query": query,
            "keywords": hints or [query],
            "wiki_query": query,
        }

    vector_query = str(rewrite.get("vector_query") or query)
    keywords = [str(k) for k in rewrite.get("keywords") or [] if str(k).strip()]
    wiki_query = str(rewrite.get("wiki_query") or query)
    fts_query = " ".join(keywords) if keywords else vector_query

    recall_limit = PRECISE_SEARCH_RECALL_PER_CHANNEL
    vector_hits = await _search_vector_channel(
        session, task_id, vector_query, limit=recall_limit
    )
    fts_hits = await search_fts(session, task_id, fts_query, limit=recall_limit)
    wiki_hits = await search_wiki(
        session, task_id, wiki_query, limit=recall_limit
    )

    vector_ids = {chunk_id for chunk_id, _ in vector_hits}
    keyword_ids = {str(hit["chunk_id"]) for hit in fts_hits}
    wiki_ids = {chunk_id for chunk_id, _ in wiki_hits}

    channel_chunk_ids = vector_ids | keyword_ids | wiki_ids
    title_by_id: dict[str, str] = {}
    if channel_chunk_ids:
        title_result = await session.execute(
            select(KnowledgeChunk.chunk_id, KnowledgeChunk.title).where(
                KnowledgeChunk.task_id == task_id,
                KnowledgeChunk.chunk_id.in_(list(channel_chunk_ids)),
            )
        )
        title_by_id = {row[0]: row[1] or "" for row in title_result.all()}

    channels: dict[str, list[dict[str, Any]]] = {
        "vector": [
            {
                "chunk_id": chunk_id,
                "score": score,
                "title": title_by_id.get(chunk_id, ""),
            }
            for chunk_id, score in vector_hits
        ],
        "keyword": [
            {
                "chunk_id": str(hit["chunk_id"]),
                "score": float(hit["score"]),
                "title": str(hit.get("title") or "")
                or title_by_id.get(str(hit["chunk_id"]), ""),
            }
            for hit in fts_hits
        ],
        "wiki": [
            {
                "chunk_id": chunk_id,
                "score": score,
                "title": title_by_id.get(chunk_id, ""),
            }
            for chunk_id, score in wiki_hits
        ],
    }

    merged_scores = _merge_channel_scores(vector_hits, fts_hits, wiki_hits)
    if not merged_scores:
        return DebugRetrievalResult(
            mode="precise_search",
            items=[],
            index_status=index_status,
            incomplete=incomplete,
            degraded=degraded,
            error=rewrite_error,
            path_note="precise_search: rewrite → three-channel recall → merge → rerank → expand",
            trace=DebugTrace(
                rewrite={
                    "vector_query": vector_query,
                    "keywords": keywords,
                    "wiki_query": wiki_query,
                    "raw": rewrite,
                },
                channels=channels,
                merged=[],
                pre_rerank_order=[],
                post_rerank_order=[],
                ai_rerank={
                    "used": False,
                    "scores_or_ranks": [],
                    "rationale": None,
                    **(
                        {"degraded_reason": rewrite_error}
                        if rewrite_error
                        else {}
                    ),
                },
                expansions=[],
            ),
        )

    ranked_ids = sorted(
        merged_scores,
        key=lambda chunk_id: merged_scores[chunk_id],
        reverse=True,
    )[:PRECISE_SEARCH_TOP_K]

    merged_trace = [
        {
            "chunk_id": chunk_id,
            "score": merged_scores[chunk_id],
            "channel_flags": {
                "vector": chunk_id in vector_ids,
                "keyword": chunk_id in keyword_ids,
                "wiki": chunk_id in wiki_ids,
            },
        }
        for chunk_id in ranked_ids
    ]

    chunk_result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.chunk_id.in_(list(merged_scores.keys())),
        )
    )
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunk_result.scalars().all()}

    candidate_hits: list[RetrievalHit] = []
    for chunk_id in ranked_ids:
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        hit = _chunk_to_hit(chunk)
        hit.score = merged_scores[chunk_id]
        candidate_hits.append(hit)

    pre_rerank_order = [hit.chunk_id for hit in candidate_hits]

    ai_rerank_info: dict[str, Any] = {
        "used": False,
        "scores_or_ranks": [],
        "rationale": None,
    }
    if rewrite_error:
        ai_rerank_info["degraded_reason"] = rewrite_error

    post_rerank_order = list(pre_rerank_order)
    if candidate_hits:
        try:
            reranked_ids = await get_ai_reranker().rerank(
                query or fts_query,
                candidate_hits,
            )
            order = {chunk_id: idx for idx, chunk_id in enumerate(reranked_ids)}
            candidate_hits.sort(
                key=lambda hit: (order.get(hit.chunk_id, len(order)), -hit.score)
            )
            post_rerank_order = [hit.chunk_id for hit in candidate_hits]
            ai_rerank_info["used"] = True
            ai_rerank_info["scores_or_ranks"] = [
                {"chunk_id": chunk_id, "rank": rank}
                for rank, chunk_id in enumerate(post_rerank_order, start=1)
            ]
            ai_rerank_info["rationale"] = None
        except Exception as exc:  # noqa: BLE001 — debug path degrades on AI failure
            degraded = True
            post_rerank_order = list(pre_rerank_order)
            ai_rerank_info["used"] = False
            ai_rerank_info["degraded_reason"] = f"rerank failed: {exc}"
            ai_rerank_info["scores_or_ranks"] = [
                {"chunk_id": chunk_id, "rank": rank}
                for rank, chunk_id in enumerate(post_rerank_order, start=1)
            ]
            ai_rerank_info["rationale"] = None

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
    expansions: list[dict[str, str]] = []
    for fine in fine_chunks:
        expanded: KnowledgeChunk | None = None
        reason = "no_large_ancestor"
        for anc_id in _parse_json_list(fine.ancestor_node_ids):
            large = large_by_node.get(anc_id)
            if large is not None:
                expanded = large
                reason = "ancestor_large"
                break
        if expanded is None:
            large = large_by_node.get(fine.node_id)
            if large is not None:
                expanded = large
                reason = "same_node_large"
        target = expanded or fine
        fine_to_expanded[fine.chunk_id] = target
        if target.chunk_id != fine.chunk_id:
            expansions.append(
                {
                    "from_fine_id": fine.chunk_id,
                    "to_large_id": target.chunk_id,
                    "reason": reason,
                }
            )

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

    return DebugRetrievalResult(
        mode="precise_search",
        items=final_hits,
        index_status=index_status,
        incomplete=incomplete,
        degraded=degraded,
        error=rewrite_error,
        path_note="precise_search: rewrite → three-channel recall → merge → rerank → expand",
        trace=DebugTrace(
            rewrite={
                "vector_query": vector_query,
                "keywords": keywords,
                "wiki_query": wiki_query,
                "raw": rewrite,
            },
            channels=channels,
            merged=merged_trace,
            pre_rerank_order=pre_rerank_order,
            post_rerank_order=post_rerank_order,
            ai_rerank=ai_rerank_info,
            expansions=expansions,
        ),
    )
