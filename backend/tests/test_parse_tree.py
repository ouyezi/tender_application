from __future__ import annotations

from pathlib import Path

from app.services.parse.tree import build_document_tree, flatten_nodes


FIXTURE = Path(__file__).parent / "fixtures" / "sample_with_toc.md"


def test_toc_region_not_in_section_nodes():
    md = FIXTURE.read_text(encoding="utf-8")
    tree = build_document_tree(md)
    nodes = flatten_nodes(tree)
    titles = [n["title"] for n in nodes]
    assert "目录" not in titles
    assert any("总则" in t for t in titles)
    assert any("目的" in t for t in titles)


def test_subtree_end_covers_children():
    md = FIXTURE.read_text(encoding="utf-8")
    tree = build_document_tree(md)
    nodes = {n["id"]: n for n in flatten_nodes(tree)}
    chapter1 = next(n for n in nodes.values() if "总则" in n["title"] and n["level"] <= 2)
    child = next(n for n in nodes.values() if n.get("parent_id") == chapter1["id"])
    assert chapter1["subtree_end"] >= child["end_offset"]
    assert chapter1["self_start"] <= chapter1["start_offset"]


def test_numbering_levels():
    md = FIXTURE.read_text(encoding="utf-8")
    nodes = flatten_nodes(build_document_tree(md))
    purpose = next(n for n in nodes if "目的" in n["title"])
    assert purpose["numbering"].startswith("1.1") or purpose["level"] >= 2


def test_no_headings_fallback():
    tree = build_document_tree("just plain text, no headings at all.")
    assert "no_headings" in tree["warnings"]
    assert len(tree["nodes"]) == 1
    node = tree["nodes"][0]
    assert node["title"] == "全文"
    assert node["source"] == "heading"
