from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

from app.services.agent_os import AgentOSClient

RETRIEVAL_QUERY_REWRITER_APP_NAME = "retrieval_query_rewriter_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class QueryRewriteResponseError(ValueError):
    pass


class AgentOSQueryRewriter:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_QUERY_REWRITER_APP_NAME,
        client: Optional[AgentOSClient] = None,
        invoke_app: Optional[InvokeFn] = None,
    ) -> None:
        self.app_name = app_name
        self._client = client
        self._invoke_app = invoke_app

    async def _invoke(self, input_data: dict[str, object]) -> dict[str, object]:
        if self._invoke_app is not None:
            return await self._invoke_app(self.app_name, input_data)
        client = self._client or AgentOSClient()
        return await client.invoke_app(self.app_name, input_data)

    async def rewrite(
        self,
        query: str,
        hints: list[str] | None = None,
    ) -> dict[str, object]:
        payload = await self._invoke(
            {
                "query": query,
                "hints_json": json.dumps(hints or [], ensure_ascii=False),
            }
        )
        vector_query = payload.get("vector_query")
        wiki_query = payload.get("wiki_query")
        if not isinstance(vector_query, str) or not vector_query.strip():
            raise QueryRewriteResponseError("vector_query invalid")
        if not isinstance(wiki_query, str) or not wiki_query.strip():
            raise QueryRewriteResponseError("wiki_query invalid")
        raw_keywords = payload.get("keywords_json")
        if isinstance(raw_keywords, str):
            try:
                keywords = json.loads(raw_keywords)
            except json.JSONDecodeError as exc:
                raise QueryRewriteResponseError("keywords_json invalid") from exc
        elif isinstance(raw_keywords, list):
            keywords = raw_keywords
        else:
            raise QueryRewriteResponseError("keywords_json missing")
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise QueryRewriteResponseError("keywords must be string list")
        return {
            "vector_query": vector_query.strip(),
            "keywords": keywords,
            "wiki_query": wiki_query.strip(),
        }
