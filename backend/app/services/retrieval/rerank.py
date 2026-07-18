from __future__ import annotations

from typing import Protocol

from app.engine.base import RetrievalHit


class AiReranker(Protocol):
    async def rerank(
        self,
        requirement: str,
        hits: list[RetrievalHit],
    ) -> list[str]: ...


def get_ai_reranker() -> AiReranker:
    from app.services.retrieval.rerank_agent_os import AgentOSAiReranker

    return AgentOSAiReranker()
