from __future__ import annotations

from typing import Protocol

from app.services.retrieval.tags import map_to_controlled_tags
from app.services.retrieval.types import SegmentDraft


class ChunkEnricher(Protocol):
    async def enrich_many(
        self,
        *,
        task_id: str,
        segments: list[SegmentDraft],
        catalog: list[dict],
    ) -> list[SegmentDraft]: ...


def _collect_raw_labels(segment: SegmentDraft, catalog: list[dict]) -> list[str]:
    raw_labels = list(segment.title_path)
    haystack = segment.text
    for row in catalog:
        name = row["name"]
        if name in haystack:
            raw_labels.append(name)
        for alias in row.get("aliases") or []:
            if alias in haystack:
                raw_labels.append(alias)
    return raw_labels


class MockChunkEnricher:
    """Rule-based enricher: title path + keyword hits against the tag catalog."""

    async def enrich_many(
        self,
        *,
        task_id: str,
        segments: list[SegmentDraft],
        catalog: list[dict],
    ) -> list[SegmentDraft]:
        del task_id
        for segment in segments:
            raw_labels = _collect_raw_labels(segment, catalog)
            segment.tags = map_to_controlled_tags(raw_labels, catalog=catalog)
            segment.summary = segment.text[:120]
            segment.description = " / ".join(segment.title_path)
            if segment.title_path and not segment.title:
                segment.title = segment.title_path[-1]
        return segments
