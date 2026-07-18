from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import INDEX_TAG_MIN_CONFIDENCE
from app.models import KnowledgeChunk, WikiPage
from app.services.agent_os import AgentOSClient

RETRIEVAL_WIKI_WRITER_APP_NAME = "retrieval_wiki_writer_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class WikiBuilderResponseError(ValueError):
    pass


def _parse_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


class AgentOSWikiBuilder:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_WIKI_WRITER_APP_NAME,
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

    def _group_chunks(
        self, fine_chunks: list[KnowledgeChunk]
    ) -> dict[str, list[KnowledgeChunk]]:
        grouped: dict[str, list[KnowledgeChunk]] = {}
        for chunk in fine_chunks:
            for tag in _parse_json_list(chunk.tags):
                name = tag.get("name")
                confidence = float(tag.get("confidence", 0.0))
                if not name or confidence < INDEX_TAG_MIN_CONFIDENCE:
                    continue
                grouped.setdefault(name, []).append(chunk)
        return grouped

    def _build_pages_input(
        self, grouped: dict[str, list[KnowledgeChunk]]
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        for tag_name, members in grouped.items():
            pages.append(
                {
                    "tag_name": tag_name,
                    "member_chunk_ids": [chunk.chunk_id for chunk in members],
                    "member_summaries": [
                        {
                            "chunk_id": chunk.chunk_id,
                            "title": chunk.title,
                            "summary": chunk.summary,
                        }
                        for chunk in members
                    ],
                }
            )
        return pages

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
        grouped = self._group_chunks(fine_chunks)

        if not grouped:
            return

        pages_input = self._build_pages_input(grouped)
        payload = await self._invoke(
            {
                "task_id": task_id,
                "pages_json": json.dumps(pages_input, ensure_ascii=False),
            }
        )

        raw_pages = payload.get("pages_json")
        if not isinstance(raw_pages, str):
            raise WikiBuilderResponseError("pages_json missing")
        try:
            rows = json.loads(raw_pages)
        except json.JSONDecodeError as exc:
            raise WikiBuilderResponseError("pages_json invalid") from exc
        if not isinstance(rows, list):
            raise WikiBuilderResponseError("pages_json invalid")

        by_tag: dict[str, dict[str, object]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            tag_name = row.get("tag_name")
            if isinstance(tag_name, str):
                by_tag[tag_name] = row

        for tag_name, members in grouped.items():
            row = by_tag.get(tag_name)
            if row is None:
                raise WikiBuilderResponseError(
                    f"missing wiki copy for tag {tag_name}"
                )
            title = row.get("title")
            summary = row.get("summary")
            description = row.get("description")
            if not isinstance(title, str) or not title.strip():
                raise WikiBuilderResponseError(f"title invalid for tag {tag_name}")
            if not isinstance(summary, str) or not summary.strip():
                raise WikiBuilderResponseError(f"summary invalid for tag {tag_name}")
            if not isinstance(description, str) or not description.strip():
                raise WikiBuilderResponseError(
                    f"description invalid for tag {tag_name}"
                )

            session.add(
                WikiPage(
                    task_id=task_id,
                    title=title.strip(),
                    summary=summary.strip(),
                    description=description.strip(),
                    tags=json.dumps([tag_name], ensure_ascii=False),
                    member_chunk_ids=json.dumps(
                        [chunk.chunk_id for chunk in members],
                        ensure_ascii=False,
                    ),
                )
            )
