from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from app.config import RETRIEVAL_CONTEXT_RESOLVER_APP_NAME
from app.services.agent_os import AgentOSClient

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class ContextResolverResponseError(ValueError):
    pass


def _parse_string_list(
    payload: dict[str, object],
    *,
    field: str,
    missing_message: str,
) -> object:
    raw = payload.get(field)
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ContextResolverResponseError(f"{field} invalid") from exc
    if isinstance(raw, list):
        return raw

    output = payload.get("output")
    if isinstance(output, str) and output.strip():
        try:
            parsed = json.loads(output.strip())
        except json.JSONDecodeError as exc:
            raise ContextResolverResponseError(missing_message) from exc
        if isinstance(parsed, dict):
            nested = parsed.get(field.replace("_json", ""))
            if nested is not None:
                return nested
            if field == "actions_json" and "actions" in parsed:
                return parsed["actions"]
            if field == "sibling_chunk_ids_json" and "sibling_chunk_ids" in parsed:
                return parsed["sibling_chunk_ids"]
        raise ContextResolverResponseError(missing_message)

    raise ContextResolverResponseError(missing_message)


def _require_string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContextResolverResponseError(f"{field} must be string list")
    return list(value)


class AgentOSContextResolver:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_CONTEXT_RESOLVER_APP_NAME,
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

    async def resolve_group(
        self,
        payload: dict[str, Any],
        candidates: list[str],
    ) -> dict[str, list[str]]:
        response = await self._invoke(
            {"payload_json": json.dumps(payload, ensure_ascii=False)}
        )

        actions = _require_string_list(
            _parse_string_list(
                response,
                field="actions_json",
                missing_message="actions_json missing",
            ),
            field="actions",
        )
        sibling_chunk_ids = _require_string_list(
            _parse_string_list(
                response,
                field="sibling_chunk_ids_json",
                missing_message="sibling_chunk_ids_json missing",
            ),
            field="sibling_chunk_ids",
        )

        candidate_set = set(candidates)
        if not set(actions).issubset(candidate_set):
            raise ContextResolverResponseError("actions must be subset of candidates")

        sibling_ids = {
            sibling["chunk_id"]
            for sibling in payload.get("siblings") or []
            if isinstance(sibling, dict) and isinstance(sibling.get("chunk_id"), str)
        }
        if not set(sibling_chunk_ids).issubset(sibling_ids):
            raise ContextResolverResponseError(
                "sibling_chunk_ids must be subset of input siblings"
            )

        return {
            "actions": actions,
            "sibling_chunk_ids": sibling_chunk_ids,
        }
