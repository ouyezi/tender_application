from __future__ import annotations

import json
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import WikiPage


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


def get_wiki_builder() -> WikiBuilder:
    from app.services.retrieval.wiki_agent_os import AgentOSWikiBuilder

    return AgentOSWikiBuilder()


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
