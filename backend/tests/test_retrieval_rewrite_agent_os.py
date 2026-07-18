import json
import pytest

from app.services.retrieval.rewrite_agent_os import (
    RETRIEVAL_QUERY_REWRITER_APP_NAME,
    AgentOSQueryRewriter,
    QueryRewriteResponseError,
)


@pytest.mark.asyncio
async def test_rewrite_invokes_app_with_json_hints():
    calls = []

    async def fake_invoke(app_name, input_data):
        calls.append((app_name, input_data))
        return {
            "vector_query": "七天无理由退款政策",
            "keywords_json": json.dumps(["退款", "无理由"], ensure_ascii=False),
            "wiki_query": "退款政策",
        }

    rewriter = AgentOSQueryRewriter(invoke_app=fake_invoke)
    out = await rewriter.rewrite("是否支持7天无理由", hints=["售后"])
    assert calls[0][0] == RETRIEVAL_QUERY_REWRITER_APP_NAME
    assert calls[0][1]["query"] == "是否支持7天无理由"
    assert json.loads(calls[0][1]["hints_json"]) == ["售后"]
    assert out["vector_query"] == "七天无理由退款政策"
    assert out["keywords"] == ["退款", "无理由"]
    assert out["wiki_query"] == "退款政策"


@pytest.mark.asyncio
async def test_rewrite_missing_vector_query_raises():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"keywords_json": "[]", "wiki_query": "x"}

    rewriter = AgentOSQueryRewriter(invoke_app=fake_invoke)
    with pytest.raises(QueryRewriteResponseError):
        await rewriter.rewrite("q", hints=[])
