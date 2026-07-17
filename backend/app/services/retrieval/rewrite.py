from __future__ import annotations

from typing import Protocol


class QueryRewriter(Protocol):
    async def rewrite(
        self,
        query: str,
        hints: list[str] | None = None,
    ) -> dict[str, object]: ...


class MockQueryRewriter:
    """Rule-based query rewrite for tests and local fallback."""

    _WIKI_TAG_HINTS = {
        "退款": "退款政策",
        "无理由": "退款政策",
        "七天": "退款政策",
        "7天": "退款政策",
        "售后": "售后政策",
        "质保": "售后政策",
    }

    async def rewrite(
        self,
        query: str,
        hints: list[str] | None = None,
    ) -> dict[str, object]:
        hint_list = [h.strip() for h in (hints or []) if h and h.strip()]
        keywords = list(dict.fromkeys(hint_list))

        for token in self._WIKI_TAG_HINTS:
            if token in query and token not in keywords:
                keywords.append(token)

        wiki_query = query
        for token, tag in self._WIKI_TAG_HINTS.items():
            if token in query or token in keywords:
                wiki_query = tag
                break
        if hint_list:
            for hint in hint_list:
                if hint.endswith("政策"):
                    wiki_query = hint
                    break

        return {
            "vector_query": query,
            "keywords": keywords or [query],
            "wiki_query": wiki_query,
        }


def get_query_rewriter() -> QueryRewriter:
    from app import config

    if config.AGENT_QUERY_REWRITER == "agent_os":
        from app.services.retrieval.rewrite_agent_os import AgentOSQueryRewriter

        return AgentOSQueryRewriter()
    return MockQueryRewriter()
