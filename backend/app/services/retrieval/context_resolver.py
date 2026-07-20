from __future__ import annotations


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
