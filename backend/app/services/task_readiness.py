from __future__ import annotations

from sqlalchemy import select

from app import db as database
from app.models import DiagnosisTask, IndexJob
from app.services import checklist_service, scheduler
from app.services.checklist_service import ChecklistNotAvailable


async def _bid_index_ready(session, task: DiagnosisTask) -> bool:
    if not task.bid_file_id:
        return False
    result = await session.execute(
        select(IndexJob)
        .where(
            IndexJob.task_id == task.id,
            IndexJob.file_id == task.bid_file_id,
        )
        .order_by(IndexJob.id.desc())
    )
    job = result.scalars().first()
    return job is not None and job.status == "ready"


async def _bid_index_required(task_id: str) -> bool:
    try:
        report = await checklist_service.get_report(task_id)
    except ChecklistNotAvailable:
        return True
    for category in report["categories"]:
        for item in category["items"]:
            if (item.get("diagnosis_mode") or "file") != "offline":
                return True
    return False


async def compute_task_readiness(task_id: str) -> dict:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)

        checklist_ready = task.current_checklist_generation_id is not None
        bid_index_ready = await _bid_index_ready(session, task)

    bid_index_required = await _bid_index_required(task_id)
    lane = scheduler.get_lane_state(task_id)

    diagnosis_ready = checklist_ready and (
        not bid_index_required or bid_index_ready
    )

    return {
        "checklist_ready": checklist_ready,
        "bid_index_ready": bid_index_ready,
        "bid_index_required": bid_index_required,
        "diagnosis_ready": diagnosis_ready,
        "checklist_lane_active": lane["checklist_lane_active"],
        "bid_index_lane_active": lane["bid_index_lane_active"],
        "full_run_active": lane["full_run_active"],
        "diagnosis_lane_active": lane["diagnosis_lane_active"],
    }
