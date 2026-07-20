import json
import pytest

from app.services.retrieval.enricher_agent_os import (
    RETRIEVAL_CHUNK_ENRICHER_APP_NAME,
    AgentOSChunkEnricher,
    ChunkEnrichResponseError,
)
from app.services.retrieval.types import SegmentDraft


def _seg(chunk_id: str, *, text: str = "含授权证书样本", segment_level: str = "fine") -> SegmentDraft:
    return SegmentDraft(
        chunk_id=chunk_id,
        node_id="n1",
        parent_node_id=None,
        ancestor_node_ids=[],
        segment_level=segment_level,
        title_path=["章", "节"],
        start=0,
        end=10,
        text=text,
    )


def _enrich_row(chunk_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "title": "t",
        "summary": "s",
        "description": "d",
        "tags": [],
    }


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


@pytest.mark.asyncio
async def test_enrich_splits_fine_into_multiple_invokes():
    calls: list[list[dict]] = []

    async def fake_invoke(app_name, input_data):
        del app_name
        segs = json.loads(input_data["segments_json"])
        calls.append(segs)
        return {
            "segments_json": json.dumps(
                [_enrich_row(row["chunk_id"]) for row in segs],
                ensure_ascii=False,
            )
        }

    segments = [
        _seg(f"c{i}", text="x" * 3000) for i in range(6)
    ]
    enricher = AgentOSChunkEnricher(
        invoke_app=fake_invoke,
        max_batch_chars=10_000,
        max_batch_segments=5,
    )
    out = await enricher.enrich_many(task_id="T1", segments=segments, catalog=[])
    assert len(out) == 6
    assert len(calls) >= 2
    assert all(len(call) <= 5 for call in calls)
    assert sum(len(call) for call in calls) == 6


@pytest.mark.asyncio
async def test_enrich_respects_max_segments_per_batch():
    calls: list[int] = []

    async def fake_invoke(app_name, input_data):
        del app_name
        segs = json.loads(input_data["segments_json"])
        calls.append(len(segs))
        return {
            "segments_json": json.dumps(
                [_enrich_row(row["chunk_id"]) for row in segs],
                ensure_ascii=False,
            )
        }

    segments = [_seg(f"c{i}", text="a") for i in range(10)]
    enricher = AgentOSChunkEnricher(
        invoke_app=fake_invoke,
        max_batch_chars=10_000,
        max_batch_segments=5,
    )
    await enricher.enrich_many(task_id="T1", segments=segments, catalog=[])
    assert calls
    assert max(calls) <= 5


@pytest.mark.asyncio
async def test_enrich_large_one_per_batch_with_truncation():
    payloads: list[list[dict]] = []

    async def fake_invoke(app_name, input_data):
        del app_name
        segs = json.loads(input_data["segments_json"])
        payloads.append(segs)
        return {
            "segments_json": json.dumps(
                [_enrich_row(row["chunk_id"]) for row in segs],
                ensure_ascii=False,
            )
        }

    segments = [
        _seg("lg1", text="y" * 20_000, segment_level="large"),
    ]
    enricher = AgentOSChunkEnricher(
        invoke_app=fake_invoke,
        max_large_text_chars=8_000,
    )
    await enricher.enrich_many(task_id="T1", segments=segments, catalog=[])
    assert len(payloads) == 1
    assert len(payloads[0]) == 1
    assert len(payloads[0][0]["text"]) == 8_000


@pytest.mark.asyncio
async def test_enrich_mixed_fine_and_large():
    calls: list[tuple[str, list[str]]] = []

    async def fake_invoke(app_name, input_data):
        del app_name
        segs = json.loads(input_data["segments_json"])
        layer = segs[0]["segment_level"]
        calls.append((layer, [row["chunk_id"] for row in segs]))
        return {
            "segments_json": json.dumps(
                [_enrich_row(row["chunk_id"]) for row in segs],
                ensure_ascii=False,
            )
        }

    segments = [
        _seg("c1", text="fine-a"),
        _seg("c2", text="fine-b"),
        _seg("lg1", text="large-body", segment_level="large"),
    ]
    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    out = await enricher.enrich_many(task_id="T1", segments=segments, catalog=[])
    assert {seg.chunk_id for seg in out} == {"c1", "c2", "lg1"}
    assert any(layer == "fine" for layer, _ in calls)
    assert any(layer == "large" for layer, _ in calls)
