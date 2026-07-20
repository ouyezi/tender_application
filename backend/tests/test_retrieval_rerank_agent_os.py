import json
import pytest

from app.engine.base import RetrievalHit
from app.services.retrieval.rerank_agent_os import (
    RETRIEVAL_AI_RERANKER_APP_NAME,
    AgentOSAiReranker,
    AiRerankResponseError,
)


def _hit(chunk_id: str, title: str = "t") -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        file_id="f1",
        node_id="n1",
        segment_level="fine",
        title=title,
        summary="s",
        tags=[],
        title_path=["a"],
        score=0.5,
    )


@pytest.mark.asyncio
async def test_rerank_returns_ordered_ids():
    async def fake_invoke(app_name, input_data):
        assert app_name == RETRIEVAL_AI_RERANKER_APP_NAME
        assert input_data["requirement"] == "退款"
        hits = json.loads(input_data["hits_json"])
        assert [h["chunk_id"] for h in hits] == ["c2", "c1"]
        return {"chunk_ids_json": json.dumps(["c1", "c2"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("退款", [_hit("c2"), _hit("c1")])
    assert out == ["c1", "c2"]


@pytest.mark.asyncio
async def test_rerank_falls_back_to_output_json_array():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {
            "status": "completed",
            "output": json.dumps(["c1", "c2"]),
            "structuredOutput": None,
        }

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("退款", [_hit("c2"), _hit("c1")])
    assert out == ["c1", "c2"]


@pytest.mark.asyncio
async def test_rerank_prefers_chunk_ids_json_over_output():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {
            "chunk_ids_json": json.dumps(["c1", "c2"]),
            "output": json.dumps(["c2", "c1"]),
        }

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("退款", [_hit("c2"), _hit("c1")])
    assert out == ["c1", "c2"]


@pytest.mark.asyncio
async def test_rerank_repairs_unknown_ids_to_input_order():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"chunk_ids_json": json.dumps(["c9"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("q", [_hit("c1")])
    assert out == ["c1"]


@pytest.mark.asyncio
async def test_rerank_repairs_partial_order_and_appends_missing():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"chunk_ids_json": json.dumps(["c2", "c9"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("q", [_hit("c1"), _hit("c2")])
    assert out == ["c2", "c1"]


@pytest.mark.asyncio
async def test_rerank_dedupes_duplicate_ids():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"chunk_ids_json": json.dumps(["c2", "c1", "c2"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("q", [_hit("c1"), _hit("c2")])
    assert out == ["c2", "c1"]


@pytest.mark.asyncio
async def test_rerank_empty_hits_skips_invoke():
    async def fake_invoke(app_name, input_data):
        raise AssertionError("should not invoke")

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("q", [])
    assert out == []
