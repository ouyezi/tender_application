from __future__ import annotations

from typing import Protocol

from app.services.retrieval.types import SegmentDraft


class ChunkEnricher(Protocol):
    async def enrich_many(
        self,
        *,
        task_id: str,
        segments: list[SegmentDraft],
        catalog: list[dict],
    ) -> list[SegmentDraft]: ...


def get_chunk_enricher() -> ChunkEnricher:
    from app.services.retrieval.enricher_agent_os import AgentOSChunkEnricher

    return AgentOSChunkEnricher()
