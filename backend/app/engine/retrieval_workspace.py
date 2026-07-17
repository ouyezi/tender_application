from __future__ import annotations

from typing import Any

from app import db as database
from app.engine.base import RetrievedChunk, RetrievalResult
from app.services.retrieval import provider as retrieval_provider


class WorkspaceRetrievalProvider:
    """Real retrieval provider backed by workspace knowledge chunks."""

    async def retrieve(
        self,
        *,
        task_id: str,
        content_source: str,
        content_target: dict[str, Any],
        item_hints: dict[str, Any] | None = None,
    ) -> RetrievalResult:
        async with database.SessionLocal() as session:
            return await retrieval_provider.retrieve(
                session,
                task_id=task_id,
                content_source=content_source,
                content_target=content_target,
                item_hints=item_hints,
            )

    async def retrieve_for_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[RetrievedChunk]:
        del category
        seen: set[str] = set()
        out: list[RetrievedChunk] = []

        for item in items:
            content_source = str(item.get("content_source") or "")
            content_target = dict(item.get("content_target") or {})
            result = await self.retrieve(
                task_id=task_id,
                content_source=content_source,
                content_target=content_target,
                item_hints={
                    "retrieval_hints": item.get("retrieval_hints") or [],
                },
            )
            if result.error:
                continue
            for hit in result.items:
                if hit.chunk_id in seen:
                    continue
                seen.add(hit.chunk_id)
                out.append(
                    RetrievedChunk(
                        chunk_id=hit.chunk_id,
                        text=hit.text,
                        location="/".join(hit.title_path),
                    )
                )

        return out
