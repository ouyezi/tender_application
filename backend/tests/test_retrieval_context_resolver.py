import json
from pathlib import Path

import pytest

from app.engine.base import RetrievalHit
from app.models import KnowledgeChunk
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from app.services.retrieval.context_resolver import (
    materialize_parent_body,
    materialize_parent_intro,
    merge_spans,
    resolve_context,
    rule_candidates,
    sibling_window,
    subtract_spans,
)
from app.services.retrieval.segments import materialize_segments

FIXTURES = Path(__file__).parent / "fixtures"


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


def _segment_to_chunk(seg) -> KnowledgeChunk:
    return KnowledgeChunk(
        task_id="T-UNIT",
        file_id="f1",
        chunk_id=seg.chunk_id,
        node_id=seg.node_id,
        parent_node_id=seg.parent_node_id,
        ancestor_node_ids=json.dumps(seg.ancestor_node_ids, ensure_ascii=False),
        segment_level=seg.segment_level,
        title=seg.title,
        summary=seg.summary or "",
        title_path=json.dumps(seg.title_path, ensure_ascii=False),
        start=seg.start,
        end=seg.end,
        intro_end=seg.intro_end,
        child_chunk_ids=json.dumps(seg.child_chunk_ids, ensure_ascii=False),
        text_inline=seg.text,
        index_status="ready",
    )


class _FixedResolver:
    def __init__(self, sibling_chunk_ids: list[str]):
        self._sibling_chunk_ids = sibling_chunk_ids

    async def resolve_group(self, payload, candidates):
        return {
            "actions": [action for action in candidates if action != "keep_only"],
            "sibling_chunk_ids": self._sibling_chunk_ids,
        }


@pytest.mark.asyncio
async def test_resolve_context_adds_parent_intro_and_sibling(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval.context_resolver.RETRIEVAL_PARENT_MAX_CHARS",
        10,
    )

    md = (FIXTURES / "retrieval_qualification.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree)
    segments = materialize_segments(md, tree, fine_src)
    chunks = [_segment_to_chunk(seg) for seg in segments]
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}

    subsidiary = next(chunk for chunk in chunks if chunk.title == "子公司资质")
    auth = next(chunk for chunk in chunks if chunk.title == "主公司授权书")

    subsidiary_hit = RetrievalHit(
        chunk_id=subsidiary.chunk_id,
        file_id=subsidiary.file_id,
        node_id=subsidiary.node_id,
        segment_level=subsidiary.segment_level,
        title=subsidiary.title,
        summary=subsidiary.summary,
        title_path=json.loads(subsidiary.title_path),
        tags=[],
        text=subsidiary.text_inline or "",
    )

    result, degraded = await resolve_context(
        query="独立法人资格 授权",
        requirement="响应人须为境内独立法人",
        matched_hits=[subsidiary_hit],
        chunk_by_id=chunk_by_id,
        all_chunks=chunks,
        markdown_by_file={"f1": md},
        resolver=_FixedResolver([auth.chunk_id]),
    )

    roles = {hit.context_role for hit in result}
    assert "matched" in roles
    assert "parent_intro" in roles
    assert "sibling" in roles
    assert degraded is False
