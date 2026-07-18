from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ChecklistCategory,
    ChecklistGeneration,
    ChecklistItem,
    DiagnosisResult,
    DiagnosisTask,
    IndexJob,
    KnowledgeChunk,
    ParseJob,
    WikiPage,
    WorkspaceFile,
)
from app.services import artifact
from app.services.scheduler import STOPPABLE_STATUSES, SchedulerConflict, discard_control, stop_task

logger = logging.getLogger(__name__)


def _path_outside_artifact(task_id: str, path_str: str | None) -> Path | None:
    if not path_str:
        return None
    path = Path(path_str)
    if not path.is_file():
        return None
    try:
        if path.resolve().is_relative_to(artifact.artifact_root(task_id).resolve()):
            return None
    except (OSError, ValueError):
        return path
    return path


async def delete_task(session: AsyncSession, task_id: str) -> None:
    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        raise LookupError(task_id)

    external_paths = [
        task.report_md_path,
        task.report_docx_path,
        task.interpret_md_path,
        task.interpret_html_path,
        task.tender_path,
        task.bid_path,
    ]

    if task.status in STOPPABLE_STATUSES:
        try:
            await stop_task(task_id)
        except (SchedulerConflict, LookupError):
            pass

    discard_control(task_id)

    task = await session.get(DiagnosisTask, task_id)
    if task is None:
        raise LookupError(task_id)

    task.current_checklist_generation_id = None
    await session.flush()

    await session.execute(delete(DiagnosisResult).where(DiagnosisResult.task_id == task_id))

    generation_ids = list(
        (
            await session.execute(
                select(ChecklistGeneration.id).where(ChecklistGeneration.task_id == task_id)
            )
        ).scalars().all()
    )
    if generation_ids:
        await session.execute(
            delete(ChecklistItem).where(ChecklistItem.generation_id.in_(generation_ids))
        )
        await session.execute(
            delete(ChecklistCategory).where(ChecklistCategory.generation_id.in_(generation_ids))
        )
        await session.execute(
            delete(ChecklistGeneration).where(ChecklistGeneration.id.in_(generation_ids))
        )

    await session.execute(delete(ParseJob).where(ParseJob.task_id == task_id))
    await session.execute(delete(IndexJob).where(IndexJob.task_id == task_id))
    await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.task_id == task_id))
    await session.execute(delete(WikiPage).where(WikiPage.task_id == task_id))
    await session.execute(delete(WorkspaceFile).where(WorkspaceFile.task_id == task_id))

    await session.delete(task)
    await session.commit()

    try:
        artifact.remove_artifact_root(task_id)
    except OSError as exc:
        logger.warning("failed to remove artifact root for task %s: %s", task_id, exc)

    for path_str in external_paths:
        path = _path_outside_artifact(task_id, path_str)
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("failed to unlink %s for task %s: %s", path, task_id, exc)
