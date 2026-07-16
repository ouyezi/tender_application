from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import DiagnosisTask, WorkspaceFile
from app.schemas import ContentOut, TreeNodeOut, WorkspaceDetailOut, WorkspaceFileOut, WorkspaceListItem
from app.services import artifact, parse_scheduler, workspace

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


async def _get_workspace_file(db: AsyncSession, task_id: str, file_id: str) -> WorkspaceFile:
    wf = await db.get(WorkspaceFile, file_id)
    if wf is None or wf.task_id != task_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return wf


@router.get("", response_model=list[WorkspaceListItem])
async def list_workspaces(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    return await workspace.list_workspaces(db)


@router.get("/{task_id}", response_model=WorkspaceDetailOut)
async def get_workspace(task_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    detail = await workspace.get_workspace_detail(db, task_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    return detail


@router.post("/{task_id}/files", response_model=WorkspaceFileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    task_id: str,
    label: str = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceFile:
    try:
        wf = await workspace.import_file(db, task_id=task_id, upload_file=file, label=label)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    await db.commit()
    await db.refresh(wf)
    if wf.kind == "document":
        await parse_scheduler.kick()
    return wf


@router.get("/{task_id}/files/{file_id}", response_model=WorkspaceFileOut)
async def get_file(task_id: str, file_id: str, db: AsyncSession = Depends(get_db)) -> WorkspaceFile:
    return await _get_workspace_file(db, task_id, file_id)


@router.get("/{task_id}/files/{file_id}/tree", response_model=list[TreeNodeOut])
async def get_file_tree(
    task_id: str, file_id: str, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    wf = await _get_workspace_file(db, task_id, file_id)
    try:
        return workspace.get_tree(wf)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tree not available")


@router.get("/{task_id}/files/{file_id}/content", response_model=ContentOut)
async def get_file_content(
    task_id: str,
    file_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    wf = await _get_workspace_file(db, task_id, file_id)
    try:
        return workspace.get_content(wf, node_id)
    except (FileNotFoundError, LookupError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")


@router.post("/{task_id}/files/{file_id}/reparse", response_model=WorkspaceFileOut)
async def reparse_file(
    task_id: str, file_id: str, db: AsyncSession = Depends(get_db)
) -> WorkspaceFile:
    wf = await _get_workspace_file(db, task_id, file_id)
    try:
        await workspace.reparse(db, wf)
    except workspace.ReparseConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    await db.commit()
    await db.refresh(wf)
    await parse_scheduler.kick()
    return wf


@router.get("/{task_id}/files/{file_id}/download")
async def download_file(
    task_id: str, file_id: str, db: AsyncSession = Depends(get_db)
) -> FileResponse:
    wf = await _get_workspace_file(db, task_id, file_id)
    path = Path(wf.stored_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileResponse(path, filename=wf.original_filename)


@router.get("/{task_id}/index")
async def get_index(task_id: str, db: AsyncSession = Depends(get_db)) -> PlainTextResponse:
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")
    path = artifact.artifact_root(task_id) / "index.md"
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Index not available")
    return PlainTextResponse(path.read_text(encoding="utf-8"))
