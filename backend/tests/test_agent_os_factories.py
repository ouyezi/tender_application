from __future__ import annotations

from app.services.retrieval.enricher import get_chunk_enricher
from app.services.retrieval.enricher_agent_os import AgentOSChunkEnricher
from app.services.retrieval.rerank import get_ai_reranker
from app.services.retrieval.rerank_agent_os import AgentOSAiReranker
from app.services.retrieval.rewrite import get_query_rewriter
from app.services.retrieval.rewrite_agent_os import AgentOSQueryRewriter
from app.services.retrieval.wiki import get_wiki_builder
from app.services.retrieval.wiki_agent_os import AgentOSWikiBuilder


def test_factories_default_to_agent_os_implementations():
    assert isinstance(get_chunk_enricher(), AgentOSChunkEnricher)
    assert isinstance(get_query_rewriter(), AgentOSQueryRewriter)
    assert isinstance(get_ai_reranker(), AgentOSAiReranker)
    assert isinstance(get_wiki_builder(), AgentOSWikiBuilder)
