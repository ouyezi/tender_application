from __future__ import annotations

from typing import Protocol


class QueryRewriter(Protocol):
    async def rewrite(
        self,
        query: str,
        hints: list[str] | None = None,
    ) -> dict[str, object]: ...


def get_query_rewriter() -> QueryRewriter:
    from app.services.retrieval.rewrite_agent_os import AgentOSQueryRewriter

    return AgentOSQueryRewriter()
