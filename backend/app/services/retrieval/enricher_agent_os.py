"""Agent OS adapter for chunk enrichment.

Requires the Agent OS client from the ``2026-07-17-agent-os-tender-interpretation``
plan. Until that lands, keep ``AGENT_CHUNK_ENRICHER=mock``.
"""

from __future__ import annotations

from typing import Any

from app.services.retrieval.types import SegmentDraft


def _require_agent_os_client() -> Any:
    try:
        from app.services.agent_os import AgentOSClient
    except ImportError as exc:
        raise ImportError(
            "Agent OS client is unavailable. Set AGENT_CHUNK_ENRICHER=mock or "
            "install the agent-os interpretation client first."
        ) from exc
    return AgentOSClient


class AgentOSChunkEnricher:
    """Enrich segments via Agent OS controlled-tag prompts."""

    def __init__(self) -> None:
        self._client = _require_agent_os_client()

    async def enrich_many(
        self,
        *,
        task_id: str,
        segments: list[SegmentDraft],
        catalog: list[dict],
    ) -> list[SegmentDraft]:
        payload = {
            "task_id": task_id,
            "segments": [
                {
                    "chunk_id": seg.chunk_id,
                    "title_path": seg.title_path,
                    "text": seg.text,
                }
                for seg in segments
            ],
            "catalog": catalog,
        }
        response = await self._client.invoke("chunk_enricher", payload)
        tags_by_id = {
            row["chunk_id"]: row.get("tags") or []
            for row in response.get("segments") or []
        }
        for segment in segments:
            segment.tags = tags_by_id.get(segment.chunk_id, [])
            segment.summary = (response.get("summaries") or {}).get(
                segment.chunk_id, segment.text[:120]
            )
            segment.description = " / ".join(segment.title_path)
            if segment.title_path and not segment.title:
                segment.title = segment.title_path[-1]
        return segments
