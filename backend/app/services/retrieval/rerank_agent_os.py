"""Agent OS adapter for AI reranking of retrieval hits.

Requires the Agent OS client from the ``2026-07-17-agent-os-tender-interpretation``
plan. Until that lands, keep ``AGENT_AI_RERANKER=mock``.
"""

from __future__ import annotations

from typing import Any

from app.engine.base import RetrievalHit


def _require_agent_os_client() -> Any:
    try:
        from app.services.agent_os import AgentOSClient
    except ImportError as exc:
        raise ImportError(
            "Agent OS client is unavailable. Set AGENT_AI_RERANKER=mock or "
            "install the agent-os interpretation client first."
        ) from exc
    return AgentOSClient


class AgentOSAiReranker:
    async def rerank(
        self,
        requirement: str,
        hits: list[RetrievalHit],
    ) -> list[str]:
        client = _require_agent_os_client()()
        response = await client.invoke(
            "ai_reranker",
            {
                "requirement": requirement,
                "hits": [
                    {
                        "chunk_id": hit.chunk_id,
                        "title": hit.title,
                        "summary": hit.summary,
                        "score": hit.score,
                    }
                    for hit in hits
                ],
            },
        )
        ordered = response.get("chunk_ids")
        if isinstance(ordered, list) and ordered:
            return [str(chunk_id) for chunk_id in ordered]
        return [hit.chunk_id for hit in hits]
