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
    _task_file_ids,
    _task_index_status,
    retrieve as provider_retrieve,
)
from app.services.retrieval.rerank import get_ai_reranker
from app.services.retrieval.rewrite import get_query_rewriter
from app.services.retrieval.tags import load_tag_catalog, validate_target_tags
from app.services.retrieval.wiki import search_wiki

_TYPED_MODES = frozenset({"full_document", "collection", "large_segments"})
_VALID_FILE_ROLES = frozenset({"tender", "bid"})
_SKIPPED_STAGES = ["rewrite", "vector", "keyword", "wiki", "ai_rerank"]
_PATH_NOTES = {
    "full_document": "full_document：直接读取整篇 markdown，未走查询重写与三路召回。",
    "collection": "collection：按受控标签过滤，未走查询重写与三路召回。",
    "large_segments": "large_segments：按文件角色取 large 段，未走查询重写与三路召回。",
}


class DebugConfigError(Exception):
    def __init__(self, message: str, *, allowed_tags: list[str] | None = None):
        super().__init__(message)
        self.allowed_tags = allowed_tags or []


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _rewrite_trace(
    *,
    vector_query: str,
    keywords: list[str],
    wiki_query: str,
    rewrite: dict[str, Any],
    rewrite_error: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vector_query": vector_query,
        "keywords": keywords,
        "wiki_query": wiki_query,
        "raw": rewrite,
    }
    if rewrite_error:
        payload["degraded_reason"] = rewrite_error
    return payload


def _precise_query_and_hints(
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    query = str(content_target.get("query") or "").strip()
    hints = [
        str(h).strip()
        for h in list((item_hints or {}).get("retrieval_hints") or [])
        if str(h).strip()
    ]
    if not query and hints:
        query = hints[0]
    return query, hints


async def _validate_typed_config(
    session: AsyncSession,
    content_source: str,
    content_target: dict[str, Any],
) -> None:
    if content_source == "collection":
        target_tags = list(content_target.get("target_tags") or [])
        if not target_tags:
            raise DebugConfigError("missing target_tags")
        catalog = await load_tag_catalog(session)
        allowed = sorted({row["name"] for row in catalog})
        ok, err = validate_target_tags(target_tags, set(allowed))
        if not ok:
            raise DebugConfigError(err, allowed_tags=allowed)

    if content_source in {"full_document", "large_segments"}:
        file_role = content_target.get("file_role")
        if file_role is not None and str(file_role).strip():
            role = str(file_role).strip()
            if role not in _VALID_FILE_ROLES:
                raise DebugConfigError(
                    f"invalid file_role: {role}; allowed: {sorted(_VALID_FILE_ROLES)}"
                )


async def _debug_typed_retrieve(
    session: AsyncSession,
    *,
    task_id: str,
    content_source: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None,
) -> DebugRetrievalResult:
    await _validate_typed_config(session, content_source, content_target)
    result = await provider_retrieve(
        session,
        task_id=task_id,
        content_source=content_source,
        content_target=content_target,
        item_hints=item_hints,
    )
    return DebugRetrievalResult(
        mode=result.mode,
        items=result.items,
        index_status=result.index_status,
        incomplete=result.incomplete,
        degraded=result.degraded,
        error=result.error,
        path_note=_PATH_NOTES[content_source],
        trace=DebugTrace(skipped_stages=list(_SKIPPED_STAGES)),
    )


async def retrieve_debug(
    session: AsyncSession,
    *,
    task_id: str,
    content_source: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None = None,
) -> DebugRetrievalResult:
    if not content_source:
        raise DebugConfigError("missing content_source")

    if content_source == "precise_search":
        query, hints = _precise_query_and_hints(content_target, item_hints)
        if not query and not hints:
            raise DebugConfigError("missing query")
        index_status, incomplete = await _task_index_status(session, task_id)
        return await _debug_precise_search(
            session,
            task_id=task_id,
            content_target=content_target,
            item_hints=item_hints,
            index_status=index_status,
            incomplete=incomplete,
            query=query,
        )

    if content_source in _TYPED_MODES:
        return await _debug_typed_retrieve(
            session,
            task_id=task_id,
            content_source=content_source,
            content_target=content_target,
            item_hints=item_hints,
        )

    raise DebugConfigError(f"unknown content_source: {content_source}")


async def _debug_precise_search(
    session: AsyncSession,
    *,
    task_id: str,
    content_target: dict[str, Any],
    item_hints: dict[str, Any] | None,
    index_status: str,
    incomplete: bool,
    query: str,
) -> DebugRetrievalResult:
    del content_target  # query already resolved by caller
    hints = [
        str(h).strip()
        for h in list((item_hints or {}).get("retrieval_hints") or [])
        if str(h).strip()
    ]

    degraded = False
    rewrite_error: str | None = None
    try:
        rewrite = await get_query_rewriter().rewrite(query, hints)
    except Exception as exc:  # noqa: BLE001 — debug path degrades on AI failure
        degraded = True
        rewrite_error = f"rewrite failed: {exc}"
        # Raw-query fallback: keep hints for keyword/wiki so recall can still hit.
        rewrite = {
            "vector_query": query,
            "keywords": hints or [query],
            "wiki_query": " ".join(hints) if hints else query,
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
                rewrite=_rewrite_trace(
                    vector_query=vector_query,
                    keywords=keywords,
                    wiki_query=wiki_query,
                    rewrite=rewrite,
                    rewrite_error=rewrite_error,
                ),
                channels=channels,
                merged=[],
                pre_rerank_order=[],
                post_rerank_order=[],
                ai_rerank={
                    "used": False,
                    "scores_or_ranks": [],
                    "rationale": None,
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

    tender_file_id, bid_file_id = await _task_file_ids(session, task_id)

    candidate_hits: list[RetrievalHit] = []
    for chunk_id in ranked_ids:
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        hit = _chunk_to_hit(
            chunk,
            tender_file_id=tender_file_id,
            bid_file_id=bid_file_id,
        )
        hit.score = merged_scores[chunk_id]
        candidate_hits.append(hit)

    pre_rerank_order = [hit.chunk_id for hit in candidate_hits]

    ai_rerank_info: dict[str, Any] = {
        "used": False,
        "scores_or_ranks": [],
        "rationale": None,
    }

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
        final_hit = _chunk_to_hit(
            chunk,
            tender_file_id=tender_file_id,
            bid_file_id=bid_file_id,
        )
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
            rewrite=_rewrite_trace(
                vector_query=vector_query,
                keywords=keywords,
                wiki_query=wiki_query,
                rewrite=rewrite,
                rewrite_error=rewrite_error,
            ),
            channels=channels,
            merged=merged_trace,
            pre_rerank_order=pre_rerank_order,
            post_rerank_order=post_rerank_order,
            ai_rerank=ai_rerank_info,
            expansions=expansions,
        ),
    )
