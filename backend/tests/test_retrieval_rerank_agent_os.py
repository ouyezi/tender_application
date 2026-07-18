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
async def test_rerank_unknown_id_raises():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"chunk_ids_json": json.dumps(["c9"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    with pytest.raises(AiRerankResponseError):
        await reranker.rerank("q", [_hit("c1")])
