from __future__ import annotations

from typing import Any

from app.engine.base import RetrievedChunk


class MockRetrievalProvider:
    async def retrieve_for_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[RetrievedChunk]:
        del items
        category_name = str(category.get("name", ""))
        return [
            RetrievedChunk(
                chunk_id=f"{task_id}-retrieval-1",
                text=f"Mock retrieval content for category {category_name}",
                location="mock/section-1",
            ),
            RetrievedChunk(
                chunk_id=f"{task_id}-retrieval-2",
                text=f"Additional evidence related to {category_name}",
                location="mock/section-2",
            ),
        ]
