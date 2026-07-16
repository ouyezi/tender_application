"""TOC-aware document tree builder.

Builds a hierarchical section tree from converted markdown, fusing ATX
heading levels with numbering signals (``1``, ``1.1``, ``第X章`` ...) while
excluding the table-of-contents (TOC) region from becoming real section
nodes. See docs/superpowers/specs/2026-07-16-workspace-management-design.md
§5.3 for the design rationale.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

ATX_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

TOC_HEADING_TITLE_RE = re.compile(r"^目\s*录$")

# "1." / "1.1" / "1.1.2" numbering prefix, optionally followed by a trailing
# dot, before the rest of the title text.
NUMBERING_PREFIX_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)*)\.?[ \t　]+(?P<rest>.+)$")

# "第一章" / "第1章" / "第一节" chapter-style marker.
_CN_DIGITS = "0-9一二三四五六七八九十百千零"
CHAPTER_PREFIX_RE = re.compile(rf"^第(?P<cn>[{_CN_DIGITS}]+)(?P<unit>[章节篇部])\s*(?P<rest>.*)$")

# TOC line item, e.g. "1. 第一章 总则 ............ 1" or
# "1.1 目的 ................ 1" — numbering + title + dotted leader + page.
TOC_LINE_RE = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)*)\.?\s+(?P<title>.+?)\s*[\.．·]{2,}\s*\d+\s*$",
    re.MULTILINE,
)

_CN_NUM_MAP = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(text: str) -> int | None:
    """Best-effort conversion of a small Chinese numeral to int (for chapter ordering)."""
    if text.isdigit():
        return int(text)
    if text in _CN_NUM_MAP:
        return _CN_NUM_MAP[text]
    if len(text) == 2 and text[0] == "十" and text[1] in _CN_NUM_MAP:
        return 10 + _CN_NUM_MAP[text[1]]
    if len(text) == 2 and text[1] == "十" and text[0] in _CN_NUM_MAP:
        return _CN_NUM_MAP[text[0]] * 10
    return None


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", "", title.strip())


def _extract_numbering(raw_title: str) -> tuple[str, str | None]:
    """Return (clean_title, numbering) parsed out of a heading's raw title text."""
    raw_title = raw_title.strip()
    m = NUMBERING_PREFIX_RE.match(raw_title)
    if m:
        return m.group("rest").strip(), m.group("num")
    m = CHAPTER_PREFIX_RE.match(raw_title)
    if m:
        n = _cn_to_int(m.group("cn"))
        if n is not None:
            return raw_title, str(n)
        return raw_title, None
    return raw_title, None


def _find_atx_headings(markdown: str) -> list[dict[str, Any]]:
    headings = []
    for m in ATX_HEADING_RE.finditer(markdown):
        line_start = m.start()
        line_end = m.end()
        # advance past the trailing newline so start_offset points at content
        content_start = line_end
        if content_start < len(markdown) and markdown[content_start] == "\n":
            content_start += 1
        headings.append(
            {
                "atx_level": len(m.group(1)),
                "raw_title": m.group(2).strip(),
                "self_start": line_start,
                "start_offset": content_start,
            }
        )
    return headings


def _looks_like_toc_line(raw_title: str) -> bool:
    """True if a heading candidate's text is actually a TOC dotted-leader entry."""
    return bool(re.search(r"[\.．·]{2,}\s*\d+\s*$", raw_title))


def _find_toc_region_end(markdown: str, headings: list[dict[str, Any]], toc_index: int) -> int:
    for h in headings[toc_index + 1 :]:
        if _looks_like_toc_line(h["raw_title"]):
            continue
        return h["self_start"]
    return len(markdown)


def _parse_toc_entries(toc_text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for m in TOC_LINE_RE.finditer(toc_text):
        entries[m.group("num")] = m.group("title").strip()
    return entries


def _make_node(
    *,
    node_id: str,
    title: str,
    level: int,
    numbering: str | None,
    parent_id: str | None,
    start_offset: int,
    end_offset: int,
    self_start: int,
    source: str,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": title,
        "level": level,
        "numbering": numbering,
        "parent_id": parent_id,
        "start_offset": start_offset,
        "end_offset": end_offset,
        "self_start": self_start,
        "subtree_end": end_offset,
        "source": source,
        "children": [],
    }


def _compute_subtree_end(node: dict[str, Any]) -> int:
    end = node["end_offset"]
    for child in node["children"]:
        end = max(end, _compute_subtree_end(child))
    node["subtree_end"] = end
    return end


def _build_tree_from_headings(
    markdown: str,
    headings: list[dict[str, Any]],
    toc_entries: dict[str, str],
) -> list[dict[str, Any]]:
    reverse_toc = {_normalize_title(title): num for num, title in toc_entries.items()}

    prepared: list[dict[str, Any]] = []
    for h in headings:
        clean_title, numbering = _extract_numbering(h["raw_title"])
        source = "numbering" if numbering else "heading"
        if numbering is None:
            toc_num = reverse_toc.get(_normalize_title(clean_title))
            if toc_num:
                numbering = toc_num
                source = "toc"
        level = numbering.count(".") + 1 if numbering else h["atx_level"]
        prepared.append(
            {
                "clean_title": clean_title,
                "numbering": numbering,
                "level": level,
                "self_start": h["self_start"],
                "start_offset": h["start_offset"],
                "source": source,
            }
        )

    for i, p in enumerate(prepared):
        next_self_start = prepared[i + 1]["self_start"] if i + 1 < len(prepared) else len(markdown)
        p["end_offset"] = next_self_start

    roots: list[dict[str, Any]] = []
    stack: list[dict[str, Any]] = []
    for p in prepared:
        while stack and stack[-1]["level"] >= p["level"]:
            stack.pop()
        parent_id = stack[-1]["id"] if stack else None
        node = _make_node(
            node_id=f"n_{uuid.uuid4().hex[:10]}",
            title=p["clean_title"],
            level=p["level"],
            numbering=p["numbering"],
            parent_id=parent_id,
            start_offset=p["start_offset"],
            end_offset=p["end_offset"],
            self_start=p["self_start"],
            source=p["source"],
        )
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)

    for r in roots:
        _compute_subtree_end(r)

    return roots


def build_document_tree(markdown: str) -> dict[str, Any]:
    """Build a TOC-aware section tree from converted markdown.

    Returns ``{"nodes": [...nested children...], "warnings": [...]}``.
    """
    warnings: list[str] = []
    headings = _find_atx_headings(markdown)

    toc_index = next(
        (i for i, h in enumerate(headings) if TOC_HEADING_TITLE_RE.match(h["raw_title"])),
        None,
    )

    body_headings = headings
    toc_entries: dict[str, str] = {}

    if toc_index is not None:
        toc_heading = headings[toc_index]
        region_end = _find_toc_region_end(markdown, headings, toc_index)
        toc_text = markdown[toc_heading["start_offset"] : region_end]
        toc_entries = _parse_toc_entries(toc_text)
        body_headings = [
            h
            for h in headings
            if not (toc_heading["self_start"] <= h["self_start"] < region_end)
        ]
        if not toc_entries:
            warnings.append("toc_region_no_entries")

    if not body_headings:
        end = len(markdown)
        node = _make_node(
            node_id=f"n_{uuid.uuid4().hex[:10]}",
            title="全文",
            level=1,
            numbering=None,
            parent_id=None,
            start_offset=0,
            end_offset=end,
            self_start=0,
            source="heading",
        )
        warnings.append("no_headings")
        return {"nodes": [node], "warnings": warnings}

    nodes = _build_tree_from_headings(markdown, body_headings, toc_entries)
    return {"nodes": nodes, "warnings": warnings}


def flatten_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Depth-first flatten nested ``children`` into a flat list.

    Each returned node keeps its ``parent_id`` and a ``children`` list of
    child ids (the original nested ``children`` node dicts are left intact
    on the input tree; this function returns shallow copies of each node
    with ``children`` replaced by nothing to avoid duplicating heavy data).
    """
    flat: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        copy = {k: v for k, v in node.items() if k != "children"}
        flat.append(copy)
        for child in node.get("children", []):
            visit(child)

    for root in tree.get("nodes", []):
        visit(root)

    return flat
