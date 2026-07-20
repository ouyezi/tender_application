from __future__ import annotations

from typing import Any

from app.services.retrieval.types import SegmentDraft


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


def materialize_segments(
    markdown: str,
    tree: dict[str, Any],
    fine_chunks: list[dict[str, Any]],
) -> list[SegmentDraft]:
    id_map: dict[str, dict[str, Any]] = {}
    _index_nodes(tree.get("nodes") or [], id_map)

    fines: list[SegmentDraft] = []
    for ch in fine_chunks:
        node_id = ch["node_id"]
        node = id_map.get(node_id, {})
        fines.append(
            SegmentDraft(
                chunk_id=ch["chunk_id"],
                node_id=node_id,
                parent_node_id=node.get("parent_id"),
                ancestor_node_ids=_ancestors(node_id, id_map),
                segment_level="fine",
                title_path=list(ch.get("title_path") or []),
                start=int(ch["start"]),
                end=int(ch["end"]),
                text=ch.get("text") or markdown[ch["start"] : ch["end"]],
                title=(ch.get("title_path") or [""])[-1] if ch.get("title_path") else "",
            )
        )

    fines_by_node: dict[str, list[str]] = {}
    for f in fines:
        fines_by_node.setdefault(f.node_id, []).append(f.chunk_id)
        for anc in f.ancestor_node_ids:
            fines_by_node.setdefault(anc, []).append(f.chunk_id)

    larges: list[SegmentDraft] = []
    for node_id, node in id_map.items():
        children = node.get("children") or []
        if not children:
            continue
        start = int(node["start_offset"])
        end = int(node.get("subtree_end") or node["end_offset"])
        intro_end: int | None = None
        if children:
            first_child_start = int(children[0]["start_offset"])
            if first_child_start > start:
                intro_end = first_child_start
        # rebuild title path
        cur = node
        parts = [cur["title"]]
        seen = {node_id}
        while cur.get("parent_id") and cur["parent_id"] not in seen:
            parent = id_map[cur["parent_id"]]
            parts.append(parent["title"])
            seen.add(cur["parent_id"])
            cur = parent
        title_path = list(reversed(parts))
        child_ids = list(dict.fromkeys(fines_by_node.get(node_id, [])))
        larges.append(
            SegmentDraft(
                chunk_id=f"lg_{node_id}",
                node_id=node_id,
                parent_node_id=node.get("parent_id"),
                ancestor_node_ids=_ancestors(node_id, id_map),
                segment_level="large",
                title_path=title_path,
                start=start,
                end=end,
                intro_end=intro_end,
                text=markdown[start:end],
                child_chunk_ids=child_ids,
                title=node.get("title") or "",
            )
        )

    return fines + larges


def expand_parent_hits(
    node_ids: list[str],
    segments: list[SegmentDraft],
) -> list[SegmentDraft]:
    by_node_large = {
        s.node_id: s for s in segments if s.segment_level == "large"
    }
    out: list[SegmentDraft] = []
    seen: set[str] = set()
    for nid in node_ids:
        large = by_node_large.get(nid)
        if large and large.chunk_id not in seen:
            out.append(large)
            seen.add(large.chunk_id)
    return out
