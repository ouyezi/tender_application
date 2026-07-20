from pathlib import Path

from app.services.parse.tree import build_document_tree
from app.services.parse.chunk import chunk_from_tree
from app.services.retrieval.segments import materialize_segments

FIXTURES = Path(__file__).parent / "fixtures"


def test_materialize_fine_and_large_for_parent_section():
    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree, max_chars=4000)
    segments = materialize_segments(md, tree, fine_src)

    fines = [s for s in segments if s.segment_level == "fine"]
    larges = [s for s in segments if s.segment_level == "large"]
    assert len(fines) >= 2
    assert any(s.title_path[-1] == "技术方案" or "技术方案" in s.title_path for s in larges)

    tech = next(s for s in larges if s.title_path and s.title_path[0] == "技术方案" or s.title_path[-1] == "技术方案")
    assert "架构正文甲" in tech.text
    assert "实施正文乙" in tech.text
    assert len(tech.child_chunk_ids) >= 2


def test_expand_parent_hit_returns_large_not_title_only():
    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree)
    segments = materialize_segments(md, tree, fine_src)
    from app.services.retrieval.segments import expand_parent_hits

    # Simulate hitting the parent node id of 技术方案
    parent = next(s for s in segments if s.segment_level == "large" and "技术方案" in s.title_path)
    expanded = expand_parent_hits([parent.node_id], segments)
    assert len(expanded) == 1
    assert expanded[0].segment_level == "large"
    assert "架构正文甲" in expanded[0].text


def test_materialize_large_sets_intro_end_to_first_child_start():
    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree, max_chars=4000)
    segments = materialize_segments(md, tree, fine_src)

    tech = next(
        s for s in segments
        if s.segment_level == "large" and "技术方案" in s.title_path
    )
    assert tech.intro_end is not None
    assert tech.intro_end > tech.start
    assert md[tech.start : tech.intro_end] in tech.text
    assert "架构正文甲" not in md[tech.start : tech.intro_end]
