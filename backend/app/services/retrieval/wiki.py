from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INDEX_TAG_MIN_CONFIDENCE
from app.models import KnowledgeChunk, WikiPage


class WikiBuilder(Protocol):
    async def build_for_task(
        self,
        session: AsyncSession,
        task_id: str,
    ) -> None: ...


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


class MockWikiBuilder:
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


def get_wiki_builder() -> WikiBuilder:
    from app import config

    if config.AGENT_WIKI_BUILDER == "agent_os":
        from app.services.retrieval.wiki_agent_os import AgentOSWikiBuilder

        return AgentOSWikiBuilder()
    return MockWikiBuilder()


async def search_wiki(
    session: AsyncSession,
    task_id: str,
    wiki_query: str,
    *,
    limit: int = 40,
) -> list[tuple[str, float]]:
    """Return fine chunk ids matched via wiki pages for the query tag/topic."""
    result = await session.execute(
        select(WikiPage).where(WikiPage.task_id == task_id)
    )
    pages = result.scalars().all()
    if not pages:
        return []

    query = wiki_query.strip()
    hits: list[tuple[str, float]] = []
    seen: set[str] = set()

    for page in pages:
        page_tags = _parse_json_list(page.tags)
        title_match = query and (query in page.title or page.title in query)
        tag_match = any(
            query in str(tag) or str(tag) in query for tag in page_tags
        )
        if not title_match and not tag_match:
            continue

        for chunk_id in _parse_json_list(page.member_chunk_ids):
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            hits.append((chunk_id, 1.0))
            if len(hits) >= limit:
                return hits

    return hits
