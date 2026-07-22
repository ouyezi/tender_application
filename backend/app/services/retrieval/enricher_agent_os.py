from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from app import config
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


def _segment_dict(seg: SegmentDraft, *, text: str | None = None) -> dict[str, object]:
    return {
        "chunk_id": seg.chunk_id,
        "title_path": seg.title_path,
        "text": text if text is not None else seg.text,
        "segment_level": seg.segment_level,
    }


def _batch_char_size(batch: list[dict[str, object]]) -> int:
    return len(json.dumps(batch, ensure_ascii=False))


def _fit_text_to_char_budget(seg: SegmentDraft, max_batch_chars: int) -> str:
    text = seg.text
    if _batch_char_size([_segment_dict(seg, text=text)]) <= max_batch_chars:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = text[:mid]
        if _batch_char_size([_segment_dict(seg, text=candidate)]) <= max_batch_chars:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def _prepare_fine_segment_dict(
    seg: SegmentDraft, *, max_batch_chars: int
) -> dict[str, object]:
    text = _fit_text_to_char_budget(seg, max_batch_chars)
    return _segment_dict(seg, text=text)


def _split_fine_batches(
    segments: list[SegmentDraft],
    *,
    max_chars: int,
    max_segments: int,
) -> list[list[dict[str, object]]]:
    batches: list[list[dict[str, object]]] = []
    current: list[dict[str, object]] = []
    for seg in segments:
        seg_dict = _prepare_fine_segment_dict(seg, max_batch_chars=max_chars)
        trial = current + [seg_dict]
        if current and (
            _batch_char_size(trial) > max_chars or len(trial) > max_segments
        ):
            batches.append(current)
            current = [seg_dict]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def _prepare_large_batches(
    segments: list[SegmentDraft],
    *,
    max_text_chars: int,
) -> list[list[dict[str, object]]]:
    batches: list[list[dict[str, object]]] = []
    for seg in segments:
        text = seg.text
        if len(text) > max_text_chars:
            text = text[:max_text_chars]
        batches.append([_segment_dict(seg, text=text)])
    return batches


def _build_enrich_batches(
    segments: list[SegmentDraft],
    *,
    max_chars: int,
    max_segments: int,
    max_large_text_chars: int,
) -> list[tuple[str, list[dict[str, object]]]]:
    fine = [s for s in segments if s.segment_level == "fine"]
    large = [s for s in segments if s.segment_level == "large"]
    batches: list[tuple[str, list[dict[str, object]]]] = []
    for batch in _split_fine_batches(
        fine, max_chars=max_chars, max_segments=max_segments
    ):
        batches.append(("fine", batch))
    for batch in _prepare_large_batches(large, max_text_chars=max_large_text_chars):
        batches.append(("large", batch))
    return batches


def _parse_enrich_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    raw_segments = payload.get("segments_json")
    if isinstance(raw_segments, str):
        try:
            rows = json.loads(raw_segments)
        except json.JSONDecodeError as exc:
            raise ChunkEnrichResponseError("segments_json invalid") from exc
    elif isinstance(raw_segments, list):
        rows = raw_segments
    else:
        raise ChunkEnrichResponseError("segments_json missing")
    if not isinstance(rows, list):
        raise ChunkEnrichResponseError("segments_json invalid")
    return [row for row in rows if isinstance(row, dict)]


def _collect_raw_labels(segment: SegmentDraft, catalog: list[dict]) -> list[str]:
    raw_labels = list(segment.title_path)
    haystack = segment.text
    for row in catalog:
        name = row.get("name")
        if not isinstance(name, str):
            continue
        if name in haystack:
            raw_labels.append(name)
        for alias in row.get("aliases") or []:
            if isinstance(alias, str) and alias in haystack:
                raw_labels.append(alias)
    return raw_labels


def _fallback_enrich(seg: SegmentDraft, catalog: list[dict]) -> None:
    from app.services.retrieval.tags import map_to_controlled_tags

    if seg.title_path and not seg.title:
        seg.title = seg.title_path[-1]
    text = seg.text.strip()
    seg.summary = text[:120] if text else (seg.title or seg.chunk_id)
    seg.description = " / ".join(seg.title_path) if seg.title_path else ""
    seg.tags = map_to_controlled_tags(
        _collect_raw_labels(seg, catalog),
        catalog=catalog,
    )


def _apply_enrich_row(
    seg: SegmentDraft,
    row: dict[str, object],
    alias_to_canonical: dict[str, str],
) -> None:
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


class AgentOSChunkEnricher:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_CHUNK_ENRICHER_APP_NAME,
        client: Optional[AgentOSClient] = None,
        invoke_app: Optional[InvokeFn] = None,
        max_batch_chars: int = config.ENRICH_BATCH_MAX_CHARS,
        max_batch_segments: int = config.ENRICH_BATCH_MAX_SEGMENTS,
        max_large_text_chars: int = config.ENRICH_LARGE_MAX_TEXT_CHARS,
    ) -> None:
        self.app_name = app_name
        self._client = client
        self._invoke_app = invoke_app
        self._max_batch_chars = max_batch_chars
        self._max_batch_segments = max_batch_segments
        self._max_large_text_chars = max_large_text_chars

    async def _invoke(self, input_data: dict[str, object]) -> dict[str, object]:
        if self._invoke_app is not None:
            return await self._invoke_app(self.app_name, input_data)
        client = self._client or AgentOSClient()
        return await client.invoke_app(self.app_name, input_data)

    async def _invoke_batch(
        self,
        *,
        task_id: str,
        catalog_json: str,
        batch_index: int,
        total_batches: int,
        layer: str,
        batch_dicts: list[dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        approx_chars = _batch_char_size(batch_dicts)
        try:
            payload = await self._invoke(
                {
                    "task_id": task_id,
                    "catalog_json": catalog_json,
                    "segments_json": json.dumps(batch_dicts, ensure_ascii=False),
                }
            )
        except ChunkEnrichResponseError:
            raise
        except Exception as exc:
            raise ChunkEnrichResponseError(
                f"enrich batch {batch_index}/{total_batches} failed "
                f"({layer}, {len(batch_dicts)} segments, "
                f"~{approx_chars} chars): {exc}"
            ) from exc

        rows: dict[str, dict[str, object]] = {}
        for row in _parse_enrich_rows(payload):
            chunk_id = row.get("chunk_id")
            if isinstance(chunk_id, str):
                rows[chunk_id] = row
        return rows

    async def enrich_many(
        self,
        *,
        task_id: str,
        segments: list[SegmentDraft],
        catalog: list[dict],
    ) -> list[SegmentDraft]:
        if not segments:
            return []

        catalog_json = json.dumps(catalog, ensure_ascii=False)
        batches = _build_enrich_batches(
            segments,
            max_chars=self._max_batch_chars,
            max_segments=self._max_batch_segments,
            max_large_text_chars=self._max_large_text_chars,
        )
        total_batches = len(batches)
        by_id: dict[str, dict[str, object]] = {}

        for batch_index, (layer, batch_dicts) in enumerate(batches, start=1):
            by_id.update(
                await self._invoke_batch(
                    task_id=task_id,
                    catalog_json=catalog_json,
                    batch_index=batch_index,
                    total_batches=total_batches,
                    layer=layer,
                    batch_dicts=batch_dicts,
                )
            )

        missing = [seg for seg in segments if seg.chunk_id not in by_id]
        if missing:
            retry_batches = _build_enrich_batches(
                missing,
                max_chars=self._max_batch_chars,
                max_segments=1,
                max_large_text_chars=self._max_large_text_chars,
            )
            retry_total = len(retry_batches)
            for retry_index, (layer, batch_dicts) in enumerate(retry_batches, start=1):
                by_id.update(
                    await self._invoke_batch(
                        task_id=task_id,
                        catalog_json=catalog_json,
                        batch_index=retry_index,
                        total_batches=retry_total,
                        layer=f"retry-{layer}",
                        batch_dicts=batch_dicts,
                    )
                )

        alias_to_canonical = _build_alias_to_canonical(catalog)

        for seg in segments:
            row = by_id.get(seg.chunk_id)
            if row is None:
                _fallback_enrich(seg, catalog)
                continue
            _apply_enrich_row(seg, row, alias_to_canonical)

        return segments
