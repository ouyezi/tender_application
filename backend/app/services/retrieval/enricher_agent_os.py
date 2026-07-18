from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from app.services.agent_os import AgentOSClient
from app.services.retrieval.types import SegmentDraft

RETRIEVAL_CHUNK_ENRICHER_APP_NAME = "retrieval_chunk_enricher_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class ChunkEnrichResponseError(ValueError):
    pass


def _build_alias_to_canonical(catalog: list[dict]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entry in catalog:
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        mapping[name] = name
        for alias in entry.get("aliases") or []:
            if isinstance(alias, str):
                mapping[alias] = name
    return mapping


def _filter_tags(
    raw_tags: object, alias_to_canonical: dict[str, str]
) -> list[dict[str, Any]]:
    if not isinstance(raw_tags, list):
        return []
    result: list[dict[str, Any]] = []
    for tag in raw_tags:
        if not isinstance(tag, dict):
            continue
        raw_name = tag.get("name")
        confidence = tag.get("confidence")
        if not isinstance(raw_name, str):
            continue
        canonical = alias_to_canonical.get(raw_name)
        if canonical is None:
            continue
        if not isinstance(confidence, (int, float)):
            continue
        result.append({"name": canonical, "confidence": confidence})
    return result


class AgentOSChunkEnricher:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_CHUNK_ENRICHER_APP_NAME,
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

    async def enrich_many(
        self,
        *,
        task_id: str,
        segments: list[SegmentDraft],
        catalog: list[dict],
    ) -> list[SegmentDraft]:
        payload = await self._invoke(
            {
                "task_id": task_id,
                "catalog_json": json.dumps(catalog, ensure_ascii=False),
                "segments_json": json.dumps(
                    [
                        {
                            "chunk_id": seg.chunk_id,
                            "title_path": seg.title_path,
                            "text": seg.text,
                            "segment_level": seg.segment_level,
                        }
                        for seg in segments
                    ],
                    ensure_ascii=False,
                ),
            }
        )
        raw_segments = payload.get("segments_json")
        if not isinstance(raw_segments, str):
            raise ChunkEnrichResponseError("segments_json missing")
        try:
            rows = json.loads(raw_segments)
        except json.JSONDecodeError as exc:
            raise ChunkEnrichResponseError("segments_json invalid") from exc
        if not isinstance(rows, list):
            raise ChunkEnrichResponseError("segments_json invalid")

        by_id: dict[str, dict[str, object]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            chunk_id = row.get("chunk_id")
            if isinstance(chunk_id, str):
                by_id[chunk_id] = row

        alias_to_canonical = _build_alias_to_canonical(catalog)

        for seg in segments:
            row = by_id.get(seg.chunk_id)
            if row is None:
                raise ChunkEnrichResponseError(
                    f"missing enrichment for chunk_id {seg.chunk_id}"
                )
            title = row.get("title")
            summary = row.get("summary")
            description = row.get("description")
            if isinstance(title, str):
                seg.title = title
            if isinstance(summary, str):
                seg.summary = summary
            if isinstance(description, str):
                seg.description = description
            seg.tags = _filter_tags(row.get("tags"), alias_to_canonical)

        return segments
