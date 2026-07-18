import pytest

from app.services.retrieval.tags import map_to_controlled_tags, validate_target_tags
from app.services.retrieval.types import SegmentDraft
from tests.stubs.retrieval_ai import StubChunkEnricher


def test_validate_target_tags_rejects_unknown():
    allowed = {"授权证书", "资质证明"}
    ok, err = validate_target_tags(["授权证书", "随便"], allowed)
    assert ok is False
    assert "随便" in err


def test_map_aliases_to_canonical():
    tags = map_to_controlled_tags(
        ["授权书", "七天无理由"],
        catalog=[
            {"name": "授权证书", "aliases": ["授权书"]},
            {"name": "退款政策", "aliases": ["七天无理由", "7天无理由"]},
        ],
    )
    names = {t["name"] for t in tags}
    assert names == {"授权证书", "退款政策"}


@pytest.mark.asyncio
async def test_stub_enricher_assigns_tags_from_keywords():
    enricher = StubChunkEnricher()
    seg = SegmentDraft(
        chunk_id="c1",
        node_id="n1",
        parent_node_id=None,
        ancestor_node_ids=[],
        segment_level="fine",
        title_path=["授权证书"],
        start=0,
        end=10,
        text="兹授权某某公司作为投标授权代表。",
        title="授权证书",
    )
    out = await enricher.enrich_many(
        task_id="T-1",
        segments=[seg],
        catalog=[{"name": "授权证书", "aliases": ["授权代表"]}],
    )
    assert out[0].tags
    assert out[0].tags[0]["name"] == "授权证书"
    assert out[0].summary
