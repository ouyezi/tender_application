"""Flatten table HTML artifacts and merge them into indexable segments."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from app.services.retrieval.types import SegmentDraft

TABLE_PLACEHOLDER_RE = re.compile(r"<!--\s*table:(tbl_\d+)\s*-->")


class _TableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr" and self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", "".join(self._parts)).strip()


def html_table_to_text(html: str) -> str:
    parser = _TableTextParser()
    parser.feed(html)
    return parser.text


def _index_nodes(nodes: list[dict[str, Any]], id_map: dict[str, dict[str, Any]]) -> None:
    for node in nodes:
        id_map[node["id"]] = node
        _index_nodes(node.get("children") or [], id_map)


def _ancestors(node_id: str, id_map: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    cur = id_map.get(node_id)
    seen = {node_id}
    while cur and cur.get("parent_id") and cur["parent_id"] not in seen:
        pid = cur["parent_id"]
        out.append(pid)
        seen.add(pid)
        cur = id_map.get(pid)
    return out


def _title_path(node: dict[str, Any], id_map: dict[str, dict[str, Any]]) -> list[str]:
    parts = [node["title"]]
    seen = {node["id"]}
    cur = node
    while cur.get("parent_id") and cur["parent_id"] not in seen:
        parent = id_map[cur["parent_id"]]
        parts.append(parent["title"])
        seen.add(cur["parent_id"])
        cur = parent
    return list(reversed(parts))


def _node_at_offset(tree: dict[str, Any], offset: int) -> dict[str, Any] | None:
    id_map: dict[str, dict[str, Any]] = {}
    _index_nodes(tree.get("nodes") or [], id_map)
    best: dict[str, Any] | None = None
    best_span = -1
    for node in id_map.values():
        start = int(node["start_offset"])
        end = int(node.get("subtree_end") or node["end_offset"])
        if start <= offset < end:
            span = end - start
            if span < best_span or best is None:
                best = node
                best_span = span
    return best


def _append_table_to_segments(
    segments: list[SegmentDraft],
    offset: int,
    tbl_id: str,
    table_text: str,
) -> bool:
    append = f"\n\n[表格 {tbl_id}]\n{table_text}"
    matched = False
    for seg in segments:
        if seg.start <= offset < seg.end:
            seg.text = seg.text.rstrip() + append
            matched = True
    return matched


def merge_table_text_into_segments(
    markdown: str,
    tree: dict[str, Any],
    segments: list[SegmentDraft],
    table_dir: Path,
) -> list[SegmentDraft]:
    """Append flattened table HTML into fine/large segments near placeholders."""
    if not table_dir.is_dir():
        return segments

    out = list(segments)
    id_map: dict[str, dict[str, Any]] = {}
    _index_nodes(tree.get("nodes") or [], id_map)

    for match in TABLE_PLACEHOLDER_RE.finditer(markdown):
        tbl_id = match.group(1)
        html_path = table_dir / f"{tbl_id}.html"
        if not html_path.is_file():
            continue
        table_text = html_table_to_text(html_path.read_text(encoding="utf-8"))
        if not table_text.strip():
            continue

        offset = match.start()
        if _append_table_to_segments(out, offset, tbl_id, table_text):
            continue

        node = _node_at_offset(tree, offset)
        if node is None:
            node = (tree.get("nodes") or [{}])[0] if tree.get("nodes") else None
        if node is None:
            continue

        node_id = node["id"]
        placeholder = match.group(0)
        out.append(
            SegmentDraft(
                chunk_id=f"tbl_{tbl_id}",
                node_id=node_id,
                parent_node_id=node.get("parent_id"),
                ancestor_node_ids=_ancestors(node_id, id_map),
                segment_level="fine",
                title_path=_title_path(node, id_map),
                start=offset,
                end=offset + len(placeholder),
                text=f"[表格 {tbl_id}]\n{table_text}",
                source="table",
                title=node.get("title") or tbl_id,
            )
        )

    return out
