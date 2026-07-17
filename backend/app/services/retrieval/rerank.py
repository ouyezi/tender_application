from __future__ import annotations

from typing import Protocol

from app.engine.base import RetrievalHit


class AiReranker(Protocol):
    async def rerank(
        self,
        requirement: str,
        hits: list[RetrievalHit],
    ) -> list[str]: ...


class MockAiReranker:
    """Deterministic reranker: prefer refund/after-sale tags, then score."""

    _PRIORITY_TAGS = ("退款政策", "售后政策")

    async def rerank(
        self,
        requirement: str,
        hits: list[RetrievalHit],
    ) -> list[str]:
        del requirement

        def _priority(hit: RetrievalHit) -> tuple[int, float]:
            tag_names = {tag.get("name") for tag in hit.tags}
            priority = 0
            for idx, name in enumerate(reversed(self._PRIORITY_TAGS), start=1):
                if name in tag_names:
                    priority = idx
            return priority, hit.score

        ordered = sorted(hits, key=_priority, reverse=True)
        return [hit.chunk_id for hit in ordered]


def get_ai_reranker() -> AiReranker:
    from app import config

    if config.AGENT_AI_RERANKER == "agent_os":
        from app.services.retrieval.rerank_agent_os import AgentOSAiReranker

        return AgentOSAiReranker()
    return MockAiReranker()
