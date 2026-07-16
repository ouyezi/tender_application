"""Chunk converted markdown for downstream retrieval, driven by the document tree.

Leaf section nodes become chunks by default; leaves whose text exceeds
``max_chars`` are further split along paragraph boundaries (falling back to
a hard character split if a single paragraph is still too long). See
docs/superpowers/specs/2026-07-16-workspace-management-design.md §5.4.
"""

from __future__ import annotations

import re
from typing import Any

PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def _index_nodes(nodes: list[dict[str, Any]], id_map: dict[str, dict[str, Any]]) -> None:
    for node in nodes:
        id_map[node["id"]] = node
        _index_nodes(node.get("children") or [], id_map)


def _iter_leaves(nodes: list[dict[str, Any]]):
    for node in nodes:
        children = node.get("children") or []
        if children:
            yield from _iter_leaves(children)
        else:
            yield node


def _title_path(node: dict[str, Any], id_map: dict[str, dict[str, Any]]) -> list[str]:
    path = [node["title"]]
    parent_id = node.get("parent_id")
    seen = {node["id"]}
    while parent_id and parent_id in id_map and parent_id not in seen:
        parent = id_map[parent_id]
        path.append(parent["title"])
        seen.add(parent_id)
        parent_id = parent.get("parent_id")
    path.reverse()
    return path


def _split_paragraph_spans(text: str, base_offset: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    for m in PARAGRAPH_SPLIT_RE.finditer(text):
        if m.start() > pos:
            spans.append((base_offset + pos, base_offset + m.start()))
        pos = m.end()
    if pos < len(text):
        spans.append((base_offset + pos, base_offset + len(text)))
    return spans


def _window_spans(spans: list[tuple[int, int]], max_chars: int) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    cur_start: int | None = None
    cur_end: int | None = None
    cur_len = 0

    for start, end in spans:
        span_len = end - start
        if cur_start is not None and cur_len > 0 and cur_len + span_len > max_chars:
            windows.append((cur_start, cur_end))  # type: ignore[arg-type]
            cur_start = None
            cur_len = 0
        if cur_start is None:
            cur_start = start
        cur_end = end
        cur_len += span_len

    if cur_start is not None:
        windows.append((cur_start, cur_end))  # type: ignore[arg-type]

    return windows


def _hard_split_oversized(windows: list[tuple[int, int]], max_chars: int) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in windows:
        if end - start <= max_chars:
            result.append((start, end))
            continue
        pos = start
        while pos < end:
            nxt = min(pos + max_chars, end)
            result.append((pos, nxt))
            pos = nxt
    return result


def chunk_from_tree(markdown: str, tree: dict[str, Any], *, max_chars: int = 4000) -> list[dict[str, Any]]:
    """Split ``markdown`` into retrieval-ready chunks driven by ``tree``'s leaf nodes.

    Each chunk dict has ``chunk_id``, ``node_id``, ``title_path`` (list of
    ancestor titles ending with the leaf's own title), ``start``, ``end``
    (absolute character offsets into ``markdown``), and ``text``.
    """
    id_map: dict[str, dict[str, Any]] = {}
    roots = tree.get("nodes", [])
    _index_nodes(roots, id_map)

    chunks: list[dict[str, Any]] = []
    for leaf in _iter_leaves(roots):
        node_id = leaf["id"]
        start_offset = leaf["start_offset"]
        end_offset = leaf["end_offset"]
        text = markdown[start_offset:end_offset]
        title_path = _title_path(leaf, id_map)

        if len(text) <= max_chars:
            windows = [(start_offset, end_offset)]
        else:
            spans = _split_paragraph_spans(text, start_offset)
            windows = _window_spans(spans, max_chars) or [(start_offset, end_offset)]
            windows = _hard_split_oversized(windows, max_chars)

        chunk_index = 0
        for start, end in windows:
            chunk_text = markdown[start:end]
            if not chunk_text.strip():
                continue
            chunks.append(
                {
                    "chunk_id": f"chk_{node_id}_{chunk_index:03d}",
                    "node_id": node_id,
                    "title_path": title_path,
                    "start": start,
                    "end": end,
                    "text": chunk_text,
                }
            )
            chunk_index += 1

    return chunks
