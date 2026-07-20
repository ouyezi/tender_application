from app.engine.base import RetrievalHit
from app.services.retrieval.context_resolver import (
    materialize_parent_body,
    materialize_parent_intro,
    merge_spans,
    rule_candidates,
    sibling_window,
    subtract_spans,
)


def test_retrieval_hit_context_fields_default():
    hit = RetrievalHit(
        chunk_id="chk_a",
        file_id="f1",
        node_id="n1",
        segment_level="fine",
        title="t",
        summary="s",
        title_path=["a"],
        tags=[],
    )
    assert hit.context_role == "matched"
    assert hit.derived_from is None
    assert hit.anchor_chunk_id is None


def test_materialize_parent_intro_slices_markdown():
    md = "INTRO\n\n## Child\nbody"
    text = materialize_parent_intro(md, start=0, intro_end=6)
    assert text == "INTRO\n"


def test_subtract_spans_removes_child_ranges():
    md = "AAAchildBBBchild2CCC"
    removed = subtract_spans(md, merge_spans([(3, 8), (8, 17)]))
    assert removed == "AAACCC"


def test_rule_candidates_r1_add_parent_intro():
    candidates = rule_candidates(
        intro_end=100,
        large_start=0,
        parent_body_chars=50,
        sibling_fine_count_under_parent=1,
        keyword_overlap=True,
    )
    assert "add_parent_intro" in candidates


def test_sibling_window_selects_neighbors():
    siblings = [
        {"chunk_id": "a", "node_id": "n1"},
        {"chunk_id": "b", "node_id": "n2"},
        {"chunk_id": "c", "node_id": "n3"},
    ]
    picked = sibling_window(siblings, anchor_node_id="n2", window=1)
    assert [s["chunk_id"] for s in picked] == ["a", "b", "c"]
