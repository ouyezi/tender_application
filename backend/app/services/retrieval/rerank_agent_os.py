from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

from app.engine.base import RetrievalHit
from app.services.agent_os import AgentOSClient

RETRIEVAL_AI_RERANKER_APP_NAME = "retrieval_ai_reranker_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class AiRerankResponseError(ValueError):
    pass


class AgentOSAiReranker:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_AI_RERANKER_APP_NAME,
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

    async def rerank(
        self,
        requirement: str,
        hits: list[RetrievalHit],
    ) -> list[str]:
        payload = await self._invoke(
            {
                "requirement": requirement,
                "hits_json": json.dumps(
                    [
                        {
                            "chunk_id": hit.chunk_id,
                            "title": hit.title,
                            "summary": hit.summary,
                            "score": hit.score,
                        }
                        for hit in hits
                    ],
                    ensure_ascii=False,
                ),
            }
        )
        raw_chunk_ids = payload.get("chunk_ids_json")
        if isinstance(raw_chunk_ids, str):
            try:
                chunk_ids = json.loads(raw_chunk_ids)
            except json.JSONDecodeError as exc:
                raise AiRerankResponseError("chunk_ids_json invalid") from exc
        elif isinstance(raw_chunk_ids, list):
            chunk_ids = raw_chunk_ids
        else:
            raise AiRerankResponseError("chunk_ids_json missing")

        if not isinstance(chunk_ids, list) or not all(isinstance(c, str) for c in chunk_ids):
            raise AiRerankResponseError("chunk_ids must be string list")

        expected = {hit.chunk_id for hit in hits}
        returned = [str(c) for c in chunk_ids]
        if set(returned) != expected or len(returned) != len(expected):
            raise AiRerankResponseError("chunk_ids must be permutation of input ids")

        return returned
