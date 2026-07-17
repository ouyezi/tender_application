from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import DiagnosisConfig, DiagnosisTask
from app.schemas import ChecklistReportOut, TaskListOut, TaskOut
from app.services import checklist_service, files, parse_scheduler, scheduler, workspace
from app.services.checklist_service import (
    ChecklistNotAvailable,
    ChecklistTaskNotFound,
    ChecklistValidationError,
)
from app.services.scheduler import SchedulerConflict

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _read_report_markdown(task: DiagnosisTask) -> str:
    if not task.report_md_path:
        return ""
    path = Path(task.report_md_path)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _read_interpret_markdown(task: DiagnosisTask) -> str:
    if not task.interpret_md_path:
        return ""
    path = Path(task.interpret_md_path)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _config_to_dict(row: DiagnosisConfig) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "technique": row.technique,
        "content_mode": row.content_mode,
        "content_scope": row.content_scope,
        "content_text": row.content_text,
        "importance": row.importance,
    }


async def _generate_task_id(db: AsyncSession) -> str:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"T-{date_str}-"
    result = await db.execute(select(DiagnosisTask.id).where(DiagnosisTask.id.like(f"{prefix}%")))
    count = len(list(result.scalars().all()))
    return f"{prefix}{count + 1:03d}"


def _task_to_out(
    task: DiagnosisTask,
    report_markdown: str = "",
    interpret_markdown: str = "",
    results: Optional[List] = None,
) -> TaskOut:
    if results is None:
        results = sorted(task.results, key=lambda r: r.sort_order)
    return TaskOut(
        id=task.id,
        tender_filename=task.tender_filename,
        bid_filename=task.bid_filename,
        tender_path=task.tender_path,
        bid_path=task.bid_path,
        background=task.background,
        requirements=task.requirements,
        status=task.status,
        progress_done=task.progress_done,
        progress_total=task.progress_total,
        report_md_path=task.report_md_path,
        report_docx_path=task.report_docx_path,
        interpret_md_path=task.interpret_md_path,
        interpret_html_path=task.interpret_html_path,
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
        results=results,
        report_markdown=report_markdown,
        interpret_markdown=interpret_markdown,
    )


@router.get("", response_model=list[TaskListOut])
async def list_tasks(db: AsyncSession = Depends(get_db)) -> list[DiagnosisTask]:
    result = await db.execute(select(DiagnosisTask).order_by(DiagnosisTask.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    background: str = Form(""),
    requirements: str = Form(""),
    tender_file: Optional[UploadFile] = File(None),
    bid_file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    if tender_file is None or not tender_file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="tender file required")
    if bid_file is None or not bid_file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="bid file required")

    task_id = await _generate_task_id(db)
    tender_filename, tender_path = await files.save_upload(tender_file, task_id, "tender")
    bid_filename, bid_path = await files.save_upload(bid_file, task_id, "bid")

    config_result = await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.id))
    configs = list(config_result.scalars().all())
    snapshot = [_config_to_dict(row) for row in configs]

    task = DiagnosisTask(
        id=task_id,
        tender_filename=tender_filename,
        tender_path=tender_path,
        bid_filename=bid_filename,
        bid_path=bid_path,
        background=background,
        requirements=requirements,
        status="interpreting",
        progress_done=0,
        progress_total=0,
        config_snapshot=json.dumps(snapshot, ensure_ascii=False),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    await workspace.register_task_documents(
        db,
        task_id=task_id,
        tender_path=tender_path,
        tender_filename=tender_filename,
        bid_path=bid_path,
        bid_filename=bid_filename,
    )
    await db.commit()
    await db.refresh(task)

    await parse_scheduler.kick()
    await scheduler.start_task(task_id)

    return _task_to_out(task, results=[])


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskOut:
    result = await db.execute(
        select(DiagnosisTask)
        .where(DiagnosisTask.id == task_id)
        .options(selectinload(DiagnosisTask.results))
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_to_out(
        task,
        report_markdown=_read_report_markdown(task),
        interpret_markdown=_read_interpret_markdown(task),
    )


@router.get("/{task_id}/checklist", response_model=ChecklistReportOut)
async def get_checklist(task_id: str) -> ChecklistReportOut:
    try:
        report = await checklist_service.get_report(task_id)
    except ChecklistTaskNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    except ChecklistNotAvailable:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Checklist not available",
        )
    except ChecklistValidationError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="checklist_data_invalid",
        )
    return ChecklistReportOut.model_validate(report)


@router.post("/{task_id}/checklist/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_checklist(task_id: str) -> dict[str, str]:
    try:
        await scheduler.retry_checklist(task_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )
    except SchedulerConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    return {"task_id": task_id, "status": "generating_checklist"}


async def _load_task_out(db: AsyncSession, task_id: str) -> TaskOut:
    result = await db.execute(
        select(DiagnosisTask)
        .where(DiagnosisTask.id == task_id)
        .options(selectinload(DiagnosisTask.results))
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return _task_to_out(
        task,
        report_markdown=_read_report_markdown(task),
        interpret_markdown=_read_interpret_markdown(task),
    )


@router.get("/{task_id}/report.docx")
async def download_report(task_id: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status != "completed" or not task.report_docx_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not available")
    path = Path(task.report_docx_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not available")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{task_id}-report.docx",
    )


@router.get("/{task_id}/interpret.html")
async def download_interpret_html(task_id: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if not task.interpret_html_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interpret report not available")
    path = Path(task.interpret_html_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interpret report not available")
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        filename=f"{task_id}-interpret.html",
    )


@router.get("/{task_id}/files/{kind}")
async def download_task_file(
    task_id: str,
    kind: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    if kind not in ("tender", "bid"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    file_path = task.tender_path if kind == "tender" else task.bid_path
    filename = task.tender_filename if kind == "tender" else task.bid_filename
    path = Path(file_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileResponse(path, filename=filename)


@router.post("/{task_id}/pause", response_model=TaskOut)
async def pause_task(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskOut:
    try:
        await scheduler.pause_task(task_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return await _load_task_out(db, task_id)


@router.post("/{task_id}/resume", response_model=TaskOut)
async def resume_task(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskOut:
    try:
        await scheduler.resume_task(task_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return await _load_task_out(db, task_id)


@router.post("/{task_id}/stop", response_model=TaskOut)
async def stop_task(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskOut:
    try:
        await scheduler.stop_task(task_id)
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return await _load_task_out(db, task_id)
