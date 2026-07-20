from __future__ import annotations

import json
import re
from typing import Any

from app.config import RETRIEVAL_PARENT_MAX_CHARS, RETRIEVAL_SIBLING_WINDOW
from app.engine.base import RetrievalHit
from app.models import KnowledgeChunk
from app.services.retrieval.document_role import resolve_document_role
from app.services.retrieval.persist import load_chunk_text


def get_context_resolver():
    from app.services.retrieval.context_resolver_agent_os import AgentOSContextResolver

    return AgentOSContextResolver()


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _chunk_text(chunk: KnowledgeChunk) -> str:
    return load_chunk_text(chunk)


def _build_hit(
    chunk: KnowledgeChunk,
    *,
    chunk_id: str | None = None,
    text: str | None = None,
    context_role: str = "matched",
    derived_from: str | None = None,
    anchor_chunk_id: str | None = None,
    tender_file_id: str | None = None,
    bid_file_id: str | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id or chunk.chunk_id,
        file_id=chunk.file_id,
        node_id=chunk.node_id,
        segment_level=chunk.segment_level,
        title=chunk.title,
        summary=chunk.summary,
        title_path=_parse_json_list(chunk.title_path),
        tags=_parse_json_list(chunk.tags),
        text=text if text is not None else _chunk_text(chunk),
        child_chunk_ids=_parse_json_list(chunk.child_chunk_ids),
        document_role=resolve_document_role(
            file_id=chunk.file_id,
            tender_file_id=tender_file_id,
            bid_file_id=bid_file_id,
            stored_role=chunk.document_role,
        ),
        context_role=context_role,
        derived_from=derived_from,
        anchor_chunk_id=anchor_chunk_id,
    )


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s[0])
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in merge_spans(spans):
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def materialize_parent_intro(markdown: str, *, start: int, intro_end: int) -> str:
    return markdown[start:intro_end]


def materialize_parent_body(
    markdown: str,
    *,
    start: int,
    end: int,
    exclude_spans: list[tuple[int, int]],
) -> str:
    body = markdown[start:end]
    # map absolute spans to body-relative
    rel = [(s - start, e - start) for s, e in exclude_spans if e > start and s < end]
    rel = [(max(0, s), min(len(body), e)) for s, e in rel]
    return subtract_spans(body, rel)


def rule_candidates(
    *,
    intro_end: int | None,
    large_start: int,
    parent_body_chars: int,
    sibling_fine_count_under_parent: int,
    keyword_overlap: bool,
) -> list[str]:
    candidates: list[str] = []
    intro_chars = (intro_end - large_start) if intro_end and intro_end > large_start else 0

    if intro_chars > 0:
        candidates.append("add_parent_intro")
    if sibling_fine_count_under_parent >= 2:
        candidates.append("add_parent_body")
    if intro_chars > RETRIEVAL_PARENT_MAX_CHARS or parent_body_chars > RETRIEVAL_PARENT_MAX_CHARS:
        candidates.append("add_siblings")
    if keyword_overlap and "add_siblings" not in candidates and intro_chars > RETRIEVAL_PARENT_MAX_CHARS:
        candidates.append("add_siblings")
    if not candidates:
        candidates.append("keep_only")
    return list(dict.fromkeys(candidates))


def sibling_window(
    siblings: list[dict],
    *,
    anchor_node_id: str,
    window: int,
) -> list[dict]:
    if not siblings:
        return []
    idx = next((i for i, s in enumerate(siblings) if s["node_id"] == anchor_node_id), 0)
    lo = max(0, idx - window)
    hi = min(len(siblings), idx + window + 1)
    return siblings[lo:hi]


def _keyword_overlap(query: str, large: KnowledgeChunk) -> bool:
    title_path = _parse_json_list(large.title_path)
    haystack = " ".join([large.title, large.summary, *title_path])
    tokens = [token for token in re.split(r"[\s，。、；：""''（）()]+", query.strip()) if token]
    return any(token in haystack for token in tokens)


def _nearest_large_ancestor(
    fine: KnowledgeChunk,
    large_by_node: dict[str, KnowledgeChunk],
) -> KnowledgeChunk | None:
    for anc_id in _parse_json_list(fine.ancestor_node_ids):
        large = large_by_node.get(anc_id)
        if large is not None:
            return large
    return large_by_node.get(fine.node_id)


def _fine_siblings_under_parent(
    all_chunks: list[KnowledgeChunk],
    *,
    parent_node_id: str,
) -> list[KnowledgeChunk]:
    siblings = [
        chunk
        for chunk in all_chunks
        if chunk.segment_level == "fine" and chunk.parent_node_id == parent_node_id
    ]
    return sorted(siblings, key=lambda chunk: chunk.start)


def _sibling_dicts(
    siblings: list[KnowledgeChunk],
    *,
    anchor_node_id: str,
) -> list[dict[str, Any]]:
    anchor_idx = next(
        (index for index, sibling in enumerate(siblings) if sibling.node_id == anchor_node_id),
        0,
    )
    out: list[dict[str, Any]] = []
    for index, sibling in enumerate(siblings):
        out.append(
            {
                "chunk_id": sibling.chunk_id,
                "node_id": sibling.node_id,
                "title": sibling.title,
                "summary": sibling.summary,
                "title_path": _parse_json_list(sibling.title_path),
                "distance": abs(index - anchor_idx),
            }
        )
    return out


def _fallback_actions(
    *,
    candidates: list[str],
    intro_end: int | None,
    large_start: int,
    window_siblings: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    actions: list[str] = []
    sibling_chunk_ids: list[str] = []
    intro_chars = (intro_end - large_start) if intro_end and intro_end > large_start else 0
    if intro_chars > 0:
        actions.append("add_parent_intro")
    if "add_siblings" in candidates:
        actions.append("add_siblings")
        sibling_chunk_ids = [sibling["chunk_id"] for sibling in window_siblings]
    if not actions:
        actions.append("keep_only")
    return actions, sibling_chunk_ids


def _exclude_spans_for_parent(
    hits: list[RetrievalHit],
    chunk_by_id: dict[str, KnowledgeChunk],
    *,
    parent_node_id: str,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for hit in hits:
        if hit.context_role not in {"matched", "sibling"}:
            continue
        chunk = chunk_by_id.get(hit.chunk_id)
        if chunk is None:
            continue
        if chunk.segment_level != "fine" or chunk.parent_node_id != parent_node_id:
            continue
        spans.append((chunk.start, chunk.end))
    return spans


async def resolve_context(
    *,
    query: str,
    requirement: str,
    matched_hits: list[RetrievalHit],
    chunk_by_id: dict[str, KnowledgeChunk],
    all_chunks: list[KnowledgeChunk],
    markdown_by_file: dict[str, str],
    resolver=None,
    tender_file_id: str | None = None,
    bid_file_id: str | None = None,
) -> tuple[list[RetrievalHit], bool]:
    output: list[RetrievalHit] = list(matched_hits)
    degraded = False

    large_by_node = {
        chunk.node_id: chunk
        for chunk in all_chunks
        if chunk.segment_level == "large"
    }

    fine_groups: dict[str, list[RetrievalHit]] = {}
    for hit in matched_hits:
        if hit.segment_level != "fine":
            continue
        chunk = chunk_by_id.get(hit.chunk_id)
        if chunk is None or not chunk.parent_node_id:
            continue
        fine_groups.setdefault(chunk.parent_node_id, []).append(hit)

    for parent_node_id, group_hits in fine_groups.items():
        anchor_hit = group_hits[0]
        anchor_chunk = chunk_by_id[anchor_hit.chunk_id]
        large = _nearest_large_ancestor(anchor_chunk, large_by_node)
        if large is None:
            continue

        markdown = markdown_by_file.get(large.file_id, "")
        siblings = _fine_siblings_under_parent(all_chunks, parent_node_id=large.node_id)
        sibling_payload = _sibling_dicts(siblings, anchor_node_id=anchor_chunk.node_id)
        window_siblings = sibling_window(
            sibling_payload,
            anchor_node_id=anchor_chunk.node_id,
            window=RETRIEVAL_SIBLING_WINDOW,
        )

        intro_end = large.intro_end
        parent_body_chars = large.end - large.start
        candidates = rule_candidates(
            intro_end=intro_end,
            large_start=large.start,
            parent_body_chars=parent_body_chars,
            sibling_fine_count_under_parent=len(group_hits),
            keyword_overlap=_keyword_overlap(query, large),
        )

        actions: list[str]
        sibling_chunk_ids: list[str]
        used_fallback = False

        if resolver is None:
            resolver = get_context_resolver()

        payload = {
            "requirement": requirement,
            "query": query,
            "hits": [
                {
                    "chunk_id": hit.chunk_id,
                    "title": chunk_by_id[hit.chunk_id].title,
                    "summary": chunk_by_id[hit.chunk_id].summary,
                    "title_path": _parse_json_list(chunk_by_id[hit.chunk_id].title_path),
                }
                for hit in group_hits
                if hit.chunk_id in chunk_by_id
            ],
            "parent": {
                "chunk_id": large.chunk_id,
                "title": large.title,
                "summary": large.summary,
                "title_path": _parse_json_list(large.title_path),
                "intro_chars": (intro_end - large.start) if intro_end and intro_end > large.start else 0,
                "total_chars": parent_body_chars,
            },
            "siblings": window_siblings,
            "candidates": candidates,
        }
        try:
            decision = await resolver.resolve_group(payload, candidates)
            actions = list(decision.get("actions") or [])
            sibling_chunk_ids = list(decision.get("sibling_chunk_ids") or [])
        except Exception:
            used_fallback = True
            actions, sibling_chunk_ids = _fallback_actions(
                candidates=candidates,
                intro_end=intro_end,
                large_start=large.start,
                window_siblings=window_siblings,
            )

        if used_fallback:
            degraded = True

        valid_sibling_ids = {sibling["chunk_id"] for sibling in window_siblings}
        sibling_chunk_ids = [
            chunk_id for chunk_id in sibling_chunk_ids if chunk_id in valid_sibling_ids
        ]

        if "add_siblings" in actions and not sibling_chunk_ids and "add_siblings" in candidates:
            sibling_chunk_ids = [sibling["chunk_id"] for sibling in window_siblings]

        for action in actions:
            if action == "add_parent_intro":
                if not intro_end or intro_end <= large.start:
                    continue
                intro_text = materialize_parent_intro(
                    markdown,
                    start=large.start,
                    intro_end=intro_end,
                )
                if not intro_text.strip():
                    continue
                output.append(
                    _build_hit(
                        large,
                        chunk_id=f"{large.chunk_id}::intro",
                        text=intro_text,
                        context_role="parent_intro",
                        derived_from=large.chunk_id,
                        anchor_chunk_id=anchor_hit.chunk_id,
                        tender_file_id=tender_file_id,
                        bid_file_id=bid_file_id,
                    )
                )
            elif action == "add_parent_body":
                exclude_spans = _exclude_spans_for_parent(
                    output,
                    chunk_by_id,
                    parent_node_id=large.node_id,
                )
                body_text = materialize_parent_body(
                    markdown,
                    start=large.start,
                    end=large.end,
                    exclude_spans=exclude_spans,
                )
                if not body_text.strip():
                    continue
                if len(body_text) > RETRIEVAL_PARENT_MAX_CHARS:
                    continue
                output.append(
                    _build_hit(
                        large,
                        chunk_id=f"{large.chunk_id}::body",
                        text=body_text,
                        context_role="parent_body",
                        derived_from=large.chunk_id,
                        anchor_chunk_id=anchor_hit.chunk_id,
                        tender_file_id=tender_file_id,
                        bid_file_id=bid_file_id,
                    )
                )
            elif action == "add_siblings":
                for sibling_id in sibling_chunk_ids:
                    sibling_chunk = chunk_by_id.get(sibling_id)
                    if sibling_chunk is None:
                        continue
                    output.append(
                        _build_hit(
                            sibling_chunk,
                            context_role="sibling",
                            anchor_chunk_id=anchor_hit.chunk_id,
                            tender_file_id=tender_file_id,
                            bid_file_id=bid_file_id,
                        )
                    )

    deduped: list[RetrievalHit] = []
    seen: set[tuple[str, str]] = set()
    for hit in output:
        key = (hit.chunk_id, hit.context_role)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(hit)

    return deduped, degraded
