from __future__ import annotations

import sys
import types

import pytest

from app.services.retrieval.enricher import MockChunkEnricher, get_chunk_enricher
from app.services.retrieval.rerank import MockAiReranker, get_ai_reranker
from app.services.retrieval.rewrite import MockQueryRewriter, get_query_rewriter
from app.services.retrieval.wiki import MockWikiBuilder, get_wiki_builder


def test_factories_default_to_mock_implementations():
    assert isinstance(get_chunk_enricher(), MockChunkEnricher)
    assert isinstance(get_query_rewriter(), MockQueryRewriter)
    assert isinstance(get_ai_reranker(), MockAiReranker)
    assert isinstance(get_wiki_builder(), MockWikiBuilder)


def test_agent_os_enricher_requires_client(monkeypatch):
    monkeypatch.setattr("app.config.AGENT_CHUNK_ENRICHER", "agent_os")
    with pytest.raises(ImportError, match="Agent OS client is unavailable"):
        get_chunk_enricher()


@pytest.mark.asyncio
async def test_agent_os_query_rewriter_requires_client(monkeypatch):
    monkeypatch.setattr("app.config.AGENT_QUERY_REWRITER", "agent_os")
    rewriter = get_query_rewriter()
    with pytest.raises(ImportError, match="Agent OS client is unavailable"):
        await rewriter.rewrite("退款", hints=["七天无理由"])


@pytest.mark.asyncio
async def test_agent_os_rewrite_invokes_client_when_present(monkeypatch):
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def invoke(self, action: str, payload: dict):
            self.calls.append((action, payload))
            return {
                "vector_query": "向量查询",
                "keywords": ["关键词"],
                "wiki_query": "Wiki主题",
            }

    fake_module = types.ModuleType("app.services.agent_os")
    fake_module.AgentOSClient = FakeClient
    monkeypatch.setitem(sys.modules, "app.services.agent_os", fake_module)
    monkeypatch.setattr("app.config.AGENT_QUERY_REWRITER", "agent_os")

    rewriter = get_query_rewriter()
    result = await rewriter.rewrite("退款政策", hints=["七天无理由"])

    assert result == {
        "vector_query": "向量查询",
        "keywords": ["关键词"],
        "wiki_query": "Wiki主题",
    }
