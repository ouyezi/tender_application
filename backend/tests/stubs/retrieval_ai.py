"""Rule-based retrieval AI stubs for pytest (former production Mock*)."""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INDEX_TAG_MIN_CONFIDENCE
from app.engine.base import RetrievalHit
from app.models import KnowledgeChunk, WikiPage
from app.services.retrieval.tags import map_to_controlled_tags
from app.services.retrieval.types import SegmentDraft


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


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


class StubChunkEnricher:
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


class StubWikiBuilder:
    """Group fine chunks by controlled tag into task-level wiki pages."""

    async def build_for_task(
        self,
        session: AsyncSession,
        task_id: str,
    ) -> None:
        await session.execute(delete(WikiPage).where(WikiPage.task_id == task_id))

        result = await session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.task_id == task_id,
                KnowledgeChunk.segment_level == "fine",
                KnowledgeChunk.index_status == "ready",
            )
        )
        fine_chunks = result.scalars().all()

        grouped: dict[str, list[KnowledgeChunk]] = {}
        for chunk in fine_chunks:
            for tag in _parse_json_list(chunk.tags):
                name = tag.get("name")
                confidence = float(tag.get("confidence", 0.0))
                if not name or confidence < INDEX_TAG_MIN_CONFIDENCE:
                    continue
                grouped.setdefault(name, []).append(chunk)

        for tag_name, members in grouped.items():
            summaries = [chunk.summary or chunk.title for chunk in members[:3]]
            session.add(
                WikiPage(
                    task_id=task_id,
                    title=tag_name,
                    summary="；".join(s for s in summaries if s),
                    description=f"{tag_name} 主题页",
                    tags=json.dumps([tag_name], ensure_ascii=False),
                    member_chunk_ids=json.dumps(
                        [chunk.chunk_id for chunk in members],
                        ensure_ascii=False,
                    ),
                )
            )


class StubQueryRewriter:
    """Rule-based query rewrite for tests."""

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


class StubAiReranker:
    """Deterministic reranker: prefer refund/after-sale tags, then score."""

    _PRIORITY_TAGS = ("退款政策", "售后政策")

    async def rerank(
        self,
        requirement: str,
        hits: list[RetrievalHit],
    ) -> list[str]:
        del requirement

        def _priority(hit: RetrievalHit) -> tuple[int, float]:
            tag_names = {tag.get("name") for tag in hit.tags}
            priority = 0
            for idx, name in enumerate(reversed(self._PRIORITY_TAGS), start=1):
                if name in tag_names:
                    priority = idx
            return priority, hit.score

        ordered = sorted(hits, key=_priority, reverse=True)
        return [hit.chunk_id for hit in ordered]


def apply_retrieval_ai_stubs(monkeypatch) -> None:
    """Patch factories so index/retrieve tests never call real Agent OS."""
    monkeypatch.setattr(
        "app.services.retrieval.enricher.get_chunk_enricher",
        lambda: StubChunkEnricher(),
    )
    monkeypatch.setattr(
        "app.services.index_scheduler.get_chunk_enricher",
        lambda: StubChunkEnricher(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.wiki.get_wiki_builder",
        lambda: StubWikiBuilder(),
    )
    monkeypatch.setattr(
        "app.services.index_scheduler.get_wiki_builder",
        lambda: StubWikiBuilder(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.rewrite.get_query_rewriter",
        lambda: StubQueryRewriter(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.rerank.get_ai_reranker",
        lambda: StubAiReranker(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.provider.get_query_rewriter",
        lambda: StubQueryRewriter(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.provider.get_ai_reranker",
        lambda: StubAiReranker(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.debug.get_query_rewriter",
        lambda: StubQueryRewriter(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.debug.get_ai_reranker",
        lambda: StubAiReranker(),
    )
