from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

from app.engine.base import RetrievalHit
from app.services.agent_os import AgentOSClient

RETRIEVAL_AI_RERANKER_APP_NAME = "retrieval_ai_reranker_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class AiRerankResponseError(ValueError):
    pass


def _parse_chunk_ids(payload: dict[str, object]) -> object:
    raw_chunk_ids = payload.get("chunk_ids_json")
    if isinstance(raw_chunk_ids, str):
        try:
            return json.loads(raw_chunk_ids)
        except json.JSONDecodeError as exc:
            raise AiRerankResponseError("chunk_ids_json invalid") from exc
    if isinstance(raw_chunk_ids, list):
        return raw_chunk_ids

    # Agent OS may leave structuredOutput empty while still returning a bare
    # JSON array in output when formatOutput binding fails.
    output = payload.get("output")
    if isinstance(output, str) and output.strip():
        try:
            return json.loads(output.strip())
        except json.JSONDecodeError as exc:
            raise AiRerankResponseError("chunk_ids_json missing") from exc

    raise AiRerankResponseError("chunk_ids_json missing")


def _normalize_rerank_ids(returned: list[str], hits: list[RetrievalHit]) -> list[str]:
    """Repair AI output into a full permutation of input ids."""
    input_order = [hit.chunk_id for hit in hits]
    expected = set(input_order)
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk_id in returned:
        if chunk_id not in expected or chunk_id in seen:
            continue
        seen.add(chunk_id)
        ordered.append(chunk_id)
    for chunk_id in input_order:
        if chunk_id not in seen:
            ordered.append(chunk_id)
    return ordered


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
        if not hits:
            return []

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
        chunk_ids = _parse_chunk_ids(payload)

        if not isinstance(chunk_ids, list) or not all(isinstance(c, str) for c in chunk_ids):
            raise AiRerankResponseError("chunk_ids must be string list")

        expected = {hit.chunk_id for hit in hits}
        returned = [str(c) for c in chunk_ids]
        if set(returned) != expected or len(returned) != len(expected):
            return _normalize_rerank_ids(returned, hits)

        return returned
