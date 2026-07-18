import json
import pytest

from app.services.retrieval.enricher_agent_os import (
    RETRIEVAL_CHUNK_ENRICHER_APP_NAME,
    AgentOSChunkEnricher,
    ChunkEnrichResponseError,
)
from app.services.retrieval.types import SegmentDraft


def _seg(chunk_id: str) -> SegmentDraft:
    return SegmentDraft(
        chunk_id=chunk_id,
        node_id="n1",
        parent_node_id=None,
        ancestor_node_ids=[],
        segment_level="fine",
        title_path=["章", "节"],
        start=0,
        end=10,
        text="含授权证书样本",
    )


@pytest.mark.asyncio
async def test_enrich_maps_controlled_tags():
    catalog = [{"name": "授权证书", "aliases": ["授权书"]}]

    async def fake_invoke(app_name, input_data):
        assert app_name == RETRIEVAL_CHUNK_ENRICHER_APP_NAME
        assert input_data["task_id"] == "T1"
        segs = json.loads(input_data["segments_json"])
        assert segs[0]["chunk_id"] == "c1"
        return {
            "segments_json": json.dumps(
                [
                    {
                        "chunk_id": "c1",
                        "title": "授权",
                        "summary": "摘要",
                        "description": "描述",
                        "tags": [{"name": "授权证书", "confidence": 0.9}],
                    }
                ],
                ensure_ascii=False,
            )
        }

    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    out = await enricher.enrich_many(
        task_id="T1", segments=[_seg("c1")], catalog=catalog
    )
    assert out[0].title == "授权"
    assert out[0].summary == "摘要"
    assert out[0].description == "描述"
    assert out[0].tags == [{"name": "授权证书", "confidence": 0.9}]


@pytest.mark.asyncio
async def test_enrich_drops_illegal_tag_names():
    catalog = [{"name": "授权证书", "aliases": []}]

    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {
            "segments_json": json.dumps(
                [
                    {
                        "chunk_id": "c1",
                        "title": "t",
                        "summary": "s",
                        "description": "d",
                        "tags": [
                            {"name": "胡编标签", "confidence": 0.9},
                            {"name": "授权证书", "confidence": 0.8},
                        ],
                    }
                ]
            )
        }

    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    out = await enricher.enrich_many(
        task_id="T1", segments=[_seg("c1")], catalog=catalog
    )
    assert out[0].tags == [{"name": "授权证书", "confidence": 0.8}]


@pytest.mark.asyncio
async def test_enrich_missing_chunk_raises():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"segments_json": json.dumps([])}

    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    with pytest.raises(ChunkEnrichResponseError):
        await enricher.enrich_many(
            task_id="T1", segments=[_seg("c1")], catalog=[]
        )
