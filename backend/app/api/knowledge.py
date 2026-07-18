from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import DiagnosisTask
from app.services.retrieval import browse
from app.services.retrieval.debug import DebugConfigError, retrieve_debug

router = APIRouter(
    prefix="/api/workspaces/{task_id}/knowledge",
    tags=["knowledge"],
)


class DebugRetrieveIn(BaseModel):
    content_source: str
    content_target: dict[str, Any] = Field(default_factory=dict)
    item_hints: dict[str, Any] | None = None


async def _require_task(db: AsyncSession, task_id: str) -> DiagnosisTask:
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


@router.get("/chunks")
async def list_knowledge_chunks(
    task_id: str,
    q: str | None = None,
    file_id: str | None = None,
    segment_level: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    index_status: str | None = None,
    embedding_status: str | None = None,
    node_id: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_task(db, task_id)
    return await browse.list_chunks(
        db,
        task_id=task_id,
        q=q,
        file_id=file_id,
        segment_level=segment_level,
        tag=tag,
        source=source,
        index_status=index_status,
        embedding_status=embedding_status,
        node_id=node_id,
        page=page,
        page_size=page_size,
    )


@router.get("/chunks/{chunk_id}")
async def get_knowledge_chunk(
    task_id: str,
    chunk_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_task(db, task_id)
    detail = await browse.get_chunk(db, task_id=task_id, chunk_id=chunk_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chunk not found")
    return detail


@router.get("/tags")
async def list_knowledge_tags(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    await _require_task(db, task_id)
    return await browse.list_tags(db)


@router.get("/wiki")
async def list_knowledge_wiki(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    await _require_task(db, task_id)
    return await browse.list_wiki_pages(db, task_id=task_id)


@router.get("/wiki/{wiki_id}")
async def get_knowledge_wiki_page(
    task_id: str,
    wiki_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_task(db, task_id)
    page = await browse.get_wiki_page(db, task_id=task_id, wiki_id=wiki_id)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wiki page not found")
    return page


@router.get("/index-status")
async def get_knowledge_index_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_task(db, task_id)
    return await browse.get_index_status(db, task_id=task_id)


@router.post("/debug/retrieve")
async def post_debug_retrieve(
    task_id: str,
    body: DebugRetrieveIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    await _require_task(db, task_id)
    try:
        result = await retrieve_debug(
            db,
            task_id=task_id,
            content_source=body.content_source,
            content_target=body.content_target,
            item_hints=body.item_hints,
        )
    except DebugConfigError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"message": str(exc), "allowed_tags": exc.allowed_tags},
        ) from exc
    return result.to_dict()
