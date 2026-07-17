"""Agent OS adapter for precise-search query rewriting.

Requires the Agent OS client from the ``2026-07-17-agent-os-tender-interpretation``
plan. Until that lands, keep ``AGENT_QUERY_REWRITER=mock``.
"""

from __future__ import annotations

from typing import Any


def _require_agent_os_client() -> Any:
    try:
        from app.services.agent_os import AgentOSClient
    except ImportError as exc:
        raise ImportError(
            "Agent OS client is unavailable. Set AGENT_QUERY_REWRITER=mock or "
            "install the agent-os interpretation client first."
        ) from exc
    return AgentOSClient


class AgentOSQueryRewriter:
    async def rewrite(
        self,
        query: str,
        hints: list[str] | None = None,
    ) -> dict[str, object]:
        client = _require_agent_os_client()()
        response = await client.invoke(
            "query_rewriter",
            {"query": query, "hints": hints or []},
        )
        return {
            "vector_query": response.get("vector_query") or query,
            "keywords": response.get("keywords") or hints or [query],
            "wiki_query": response.get("wiki_query") or query,
        }
