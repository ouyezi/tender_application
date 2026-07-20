from app.engine.base import RetrievalHit


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
