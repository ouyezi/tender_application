from __future__ import annotations

from app.config import RETRIEVAL_PARENT_MAX_CHARS


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
