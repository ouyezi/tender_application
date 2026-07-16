from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db
from app.models import DiagnosisConfig, DiagnosisTask
from app.schemas import TaskListOut, TaskOut
from app.services import files, scheduler

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


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
        error_message=task.error_message,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
        results=results,
        report_markdown=report_markdown,
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
        status="running",
        progress_done=0,
        progress_total=len(snapshot),
        config_snapshot=json.dumps(snapshot, ensure_ascii=False),
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

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
    return _task_to_out(task)
