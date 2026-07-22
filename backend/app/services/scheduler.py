from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import Optional

from app import db as database
from app.engine.batch_diagnosis_agent_os import (
    AgentOSBatchDiagnosisEngine,
    BatchDiagnosisResponseError,
)
from app.engine.checklist_agent_os import AgentOSChecklistAgent
from app.engine.interpretation_agent_os import AgentOSInterpretationAgent
from app.engine.retrieval_mock import MockRetrievalProvider
from app.engine.retrieval_workspace import WorkspaceRetrievalProvider
from app.models import DiagnosisResult, DiagnosisTask, WorkspaceFile, utcnow
from app.services import interpret_report, report
from app.services.agent_os import (
    AgentOSClient,
    AgentOSConfigError,
    AgentOSError,
    load_settings,
)
from app.services.bid_index_wait import BidIndexBlockedError, wait_for_bid_index_ready
from app.services.checklist_service import (
    ChecklistService,
    ChecklistValidationError,
    TenderParseBlockedError,
    assert_batch_complete,
    failure_stage_for_error,
    get_report,
    wait_for_tender_parse_ready,
)
from app.services.checklist_context import ChecklistInputError
from app.services.execution_graph import get_tracker
from app.services.tender_content import TenderContentProvider, TenderContentStopped
from app.services import parse_scheduler, workspace
from sqlalchemy import select
from app.models import IndexJob


class SchedulerConflict(Exception):
    """Raised when pause/resume/stop is invalid for the current task status."""


def build_retrieval_provider():
    from app import config

    if config.RETRIEVAL_PROVIDER == "workspace":
        return WorkspaceRetrievalProvider()
    return MockRetrievalProvider()


TERMINAL_STATUSES = frozenset({"completed", "stopped", "failed"})
PAUSABLE_STATUSES = frozenset(
    {
        "interpreting",
        "generating_checklist",
        "indexing_bid",
        "diagnosing",
        "running",
    }
)
IDLE_STATUS = "draft"
STOPPABLE_STATUSES = frozenset(
    {
        "interpreting",
        "generating_checklist",
        "indexing_bid",
        "diagnosing",
        "running",
        "paused",
        "draft",
    }
)

OFFLINE_EVIDENCE = "未检索文件（线下核验项）"
OFFLINE_SUGGESTION = (
    "该项属于打印/装订/密封等线下要求，需人工核验纸质或现场材料，系统不进行文件诊断"
)


def _split_items_by_diagnosis_mode(
    items: list[dict],
) -> tuple[list[dict], list[dict]]:
    offline: list[dict] = []
    file_items: list[dict] = []
    for item in items:
        mode = item.get("diagnosis_mode") or "file"
        if mode == "offline":
            offline.append(item)
        else:
            file_items.append(item)
    return offline, file_items


def _offline_batch_result(item: dict):
    from app.engine.base import BatchItemResult

    tags: list[str] = []
    rules = item.get("consequence_rules") or {}
    if isinstance(rules, dict):
        tags = [k for k in rules if isinstance(k, str)]
    description = str(item.get("requirement") or item.get("title") or "")
    return BatchItemResult(
        checklist_item_id=item["id"],
        compliance="manual_required",
        consequence_tags=tags,
        evidence=OFFLINE_EVIDENCE,
        suggestion=OFFLINE_SUGGESTION,
        response_content="",
        description=description,
    )


def _build_tender_content_provider() -> TenderContentProvider:
    settings = load_settings()
    return TenderContentProvider(timeout_seconds=settings.parse_wait_timeout_seconds)


def _build_interpretation_agent() -> AgentOSInterpretationAgent:
    settings = load_settings()
    interpret_settings = replace(
        settings,
        timeout_seconds=settings.interpret_invoke_timeout_seconds,
    )
    return AgentOSInterpretationAgent(AgentOSClient(settings=interpret_settings))


def _build_checklist_agent() -> AgentOSChecklistAgent:
    settings = load_settings()
    checklist_settings = replace(
        settings,
        timeout_seconds=settings.checklist_invoke_timeout_seconds,
    )
    return AgentOSChecklistAgent(client=AgentOSClient(settings=checklist_settings))


@dataclass
class _TaskControl:
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: bool = False
    checklist_task: Optional[asyncio.Task] = None
    bid_index_task: Optional[asyncio.Task] = None
    diagnosis_task: Optional[asyncio.Task] = None
    full_task: Optional[asyncio.Task] = None
    paused_from_status: Optional[str] = None
    resume_mode: Optional[str] = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if not self.pause_event.is_set():
            self.pause_event.set()

    def lane_state(self) -> dict:
        return {
            "checklist_lane_active": self.checklist_task is not None
            and not self.checklist_task.done(),
            "bid_index_lane_active": self.bid_index_task is not None
            and not self.bid_index_task.done(),
            "diagnosis_lane_active": self.diagnosis_task is not None
            and not self.diagnosis_task.done(),
            "full_run_active": self.full_task is not None and not self.full_task.done(),
        }

    def any_lane_active(self) -> bool:
        return any(self.lane_state().values())

    def all_tasks(self) -> list[asyncio.Task]:
        return [
            t
            for t in (
                self.checklist_task,
                self.bid_index_task,
                self.diagnosis_task,
                self.full_task,
            )
            if t is not None and not t.done()
        ]


_controls: dict[str, _TaskControl] = {}


def get_lane_state(task_id: str) -> dict:
    ctrl = _controls.get(task_id)
    if ctrl is None:
        return {
            "checklist_lane_active": False,
            "bid_index_lane_active": False,
            "full_run_active": False,
            "diagnosis_lane_active": False,
        }
    return ctrl.lane_state()


def _get_control(task_id: str) -> _TaskControl:
    if task_id not in _controls:
        _controls[task_id] = _TaskControl()
    return _controls[task_id]


def discard_control(task_id: str) -> None:
    """Drop in-memory control state after a task is deleted."""
    ctrl = _controls.pop(task_id, None)
    if ctrl is None:
        return
    ctrl.stop_requested = True
    ctrl.pause_event.set()
    for task in ctrl.all_tasks():
        task.cancel()


async def reset_for_tests() -> None:
    """Clear in-memory scheduler state between tests."""
    tasks = []
    for ctrl in list(_controls.values()):
        ctrl.stop_requested = True
        ctrl.pause_event.set()
        for task in ctrl.all_tasks():
            task.cancel()
            tasks.append(task)
    _controls.clear()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def wait_for_idle(task_id: str, timeout: float = 15.0) -> str:
    """Poll until task is idle (draft) or reaches a terminal status."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise ValueError(f"task {task_id} not found")
            ctrl = _controls.get(task_id)
            lane_active = ctrl.any_lane_active() if ctrl else False
            if task.status in TERMINAL_STATUSES:
                return task.status
            if task.status == IDLE_STATUS and not lane_active:
                return task.status
        if loop.time() >= deadline:
            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                status = task.status if task else "missing"
            raise TimeoutError(
                f"task {task_id} did not become idle within {timeout}s (status={status})"
            )
        await asyncio.sleep(0.05)


async def wait_for_terminal(task_id: str, timeout: float = 10.0) -> str:
    """Poll until task reaches a terminal status. Returns the final status."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise ValueError(f"task {task_id} not found")
            if task.status in TERMINAL_STATUSES:
                return task.status
        if loop.time() >= deadline:
            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                status = task.status if task else "missing"
            raise TimeoutError(
                f"task {task_id} did not finish within {timeout}s (status={status})"
            )
        await asyncio.sleep(0.05)


async def start_task(task_id: str) -> None:
    """Backward-compatible alias for full pipeline run."""
    await start_run_full(task_id)


async def retry_checklist(task_id: str) -> DiagnosisTask:
    ctrl = _get_control(task_id)
    if ctrl.any_lane_active():
        raise SchedulerConflict("task_runner_active")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status != "failed":
            raise SchedulerConflict("invalid_task_status")
        if task.current_checklist_generation_id is not None:
            raise SchedulerConflict("checklist_already_available")
        if not task.interpret_md_path:
            raise SchedulerConflict("interpret_not_available")
        workspace_file = (
            await session.get(WorkspaceFile, task.tender_file_id)
            if task.tender_file_id
            else None
        )
        if workspace_file is None or workspace_file.task_id != task_id:
            raise SchedulerConflict("tender_parse_missing")
        if workspace_file.parse_status != "succeeded":
            parse_status = workspace_file.parse_status or "missing"
            raise SchedulerConflict(f"tender_parse_{parse_status}")

        task.status = "generating_checklist"
        task.error_message = None
        task.failure_stage = None
        task.finished_at = None
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        prepared = task

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.done_event.clear()
    ctrl.resume_mode = "checklist"
    ctrl.checklist_task = asyncio.create_task(_run_checklist_retry(task_id))
    return prepared


async def _run_checklist_retry(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        await ChecklistService(agent=_build_checklist_agent()).generate_for_task(task_id)

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return
            if task.current_checklist_generation_id is None:
                raise RuntimeError("checklist_generation_missing")

        try:
            await _complete_from_diagnosis(task_id)
        except Exception as exc:
            await _mark_failed(task_id, str(exc)[:240], "diagnosis")
            return
    except asyncio.CancelledError:
        raise
    except (ChecklistValidationError, ChecklistInputError, TenderParseBlockedError) as exc:
        await _handle_checklist_failure(task_id, exc)
    except Exception as exc:
        await _handle_checklist_failure(task_id, exc)
    finally:
        ctrl.done_event.set()


async def pause_task(task_id: str) -> DiagnosisTask:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status not in PAUSABLE_STATUSES:
            raise SchedulerConflict(f"cannot pause task in status {task.status}")
        paused_from = task.status
        task.status = "paused"
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        paused = task

    ctrl = _get_control(task_id)
    ctrl.paused_from_status = paused_from
    if ctrl.full_task is not None and not ctrl.full_task.done():
        ctrl.resume_mode = "full"
    elif ctrl.checklist_task is not None and not ctrl.checklist_task.done():
        ctrl.resume_mode = "checklist"
    elif ctrl.bid_index_task is not None and not ctrl.bid_index_task.done():
        ctrl.resume_mode = "bid_index"
    elif ctrl.diagnosis_task is not None and not ctrl.diagnosis_task.done():
        ctrl.resume_mode = "diagnosis"
    ctrl.pause_event.clear()
    await get_tracker(task_id).notify("diagnosis", meta={"paused": True})
    return paused


async def resume_task(task_id: str) -> DiagnosisTask:
    ctrl = _get_control(task_id)
    if ctrl.stop_requested:
        raise SchedulerConflict("cannot resume task after stop was requested")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status != "paused":
            raise SchedulerConflict(f"cannot resume task in status {task.status}")
        restored = ctrl.paused_from_status or IDLE_STATUS
        task.status = restored
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        resumed = task

    ctrl.pause_event.set()
    mode = ctrl.resume_mode
    if mode == "full" and (ctrl.full_task is None or ctrl.full_task.done()):
        ctrl.full_task = asyncio.create_task(_run_full(task_id))
    elif mode == "checklist" and (ctrl.checklist_task is None or ctrl.checklist_task.done()):
        ctrl.checklist_task = asyncio.create_task(_run_checklist_lane(task_id))
    elif mode == "bid_index" and (ctrl.bid_index_task is None or ctrl.bid_index_task.done()):
        ctrl.bid_index_task = asyncio.create_task(_run_bid_index_lane(task_id))
    elif mode == "diagnosis" and (ctrl.diagnosis_task is None or ctrl.diagnosis_task.done()):
        ctrl.diagnosis_task = asyncio.create_task(_run_diagnosis_lane(task_id))
    return resumed


async def stop_task(task_id: str) -> DiagnosisTask:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status not in STOPPABLE_STATUSES:
            raise SchedulerConflict(f"cannot stop task in status {task.status}")

    ctrl = _get_control(task_id)
    ctrl.stop_requested = True
    ctrl.pause_event.set()

    if not ctrl.any_lane_active():
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise LookupError(task_id)
            if task.status in STOPPABLE_STATUSES:
                task.status = "stopped"
                task.finished_at = utcnow()
                task.updated_at = utcnow()
                await session.commit()
                await session.refresh(task)
            ctrl.done_event.set()
            return task

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    while loop.time() < deadline:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise LookupError(task_id)
            if task.status == "stopped":
                return task
        await asyncio.sleep(0.05)

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in STOPPABLE_STATUSES:
            task.status = "stopped"
            task.finished_at = utcnow()
            task.updated_at = utcnow()
            await session.commit()
            await session.refresh(task)
        return task


async def _wait_if_paused(task_id: str) -> None:
    ctrl = _get_control(task_id)
    while not ctrl.pause_event.is_set():
        if ctrl.stop_requested:
            return
        try:
            await asyncio.wait_for(ctrl.pause_event.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            continue


def _should_stop(task_id: str) -> bool:
    return _get_control(task_id).stop_requested


async def _mark_stopped(task_id: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        if task.status in TERMINAL_STATUSES:
            return
        task.status = "stopped"
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        await session.commit()


async def _set_failure_stage(task_id: str, failure_stage: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        task.failure_stage = failure_stage
        task.updated_at = utcnow()
        await session.commit()


async def _ensure_failure_stage(task_id: str, failure_stage: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        if task.failure_stage != failure_stage:
            task.failure_stage = failure_stage
            task.updated_at = utcnow()
            await session.commit()


def _is_tender_parse_error(exc: BaseException) -> bool:
    if isinstance(exc, TenderParseBlockedError):
        return True
    if isinstance(exc, ChecklistInputError):
        message = str(exc)
        return message.startswith("tender_parse_") or message in {
            "tender_parse_missing",
            "tender_file_task_mismatch",
        }
    return False


async def _handle_checklist_failure(task_id: str, exc: BaseException) -> None:
    if _is_tender_parse_error(exc):
        await _mark_failed(task_id, str(exc), "tender_parse")
        return
    if isinstance(exc, ChecklistValidationError):
        await _ensure_failure_stage(task_id, "checklist_validation")
        return
    stage = failure_stage_for_error(
        exc if isinstance(exc, Exception) else None,
        public_message=str(exc),
    )
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        if task.status not in TERMINAL_STATUSES:
            task.status = "failed"
            task.error_message = str(exc)[:240]
            task.failure_stage = stage
            task.finished_at = utcnow()
            task.updated_at = utcnow()
            await session.commit()
        elif task.failure_stage is None:
            task.failure_stage = stage
            task.updated_at = utcnow()
            await session.commit()


async def _set_idle(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.any_lane_active():
        return
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None or task.status in TERMINAL_STATUSES:
            return
        if task.status == "paused":
            return
        task.status = IDLE_STATUS
        task.updated_at = utcnow()
        await session.commit()


async def _bid_index_ready_in_session(session, task: DiagnosisTask) -> bool:
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


async def _run_interpret_and_checklist(task_id: str, *, stop_at_checklist: bool) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None or task.status in TERMINAL_STATUSES:
            return
        if task.current_checklist_generation_id is not None:
            return
        if task.tender_file_id:
            await workspace.ensure_file_parse_enqueued(session, task.tender_file_id)
            await session.commit()
            await parse_scheduler.kick()

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None or task.status in TERMINAL_STATUSES:
            return

        need_interpret = not task.interpret_md_path
        if need_interpret and task.status not in ("diagnosing", "paused"):
            task.status = "interpreting"
            task.updated_at = utcnow()
            await session.commit()

        tender_file_id = task.tender_file_id
        background = task.background or ""
        requirements = task.requirements or ""
        need_checklist = task.current_checklist_generation_id is None

    if need_interpret:
        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        if not tender_file_id:
            await _mark_failed(
                task_id,
                "tender_file_id is required for interpretation",
                "interpreting",
            )
            return

        try:
            provider = _build_tender_content_provider()
            try:
                tender_text = await provider.wait_for_markdown(
                    task_id,
                    tender_file_id,
                    stop_requested=lambda: _should_stop(task_id),
                )
            except TenderContentStopped:
                await _mark_stopped(task_id)
                return

            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            agent = _build_interpretation_agent()
            async with get_tracker(task_id).track("interpret"):
                interpret_result = await agent.interpret(
                    task_id=task_id,
                    tender_text=tender_text,
                    background=background,
                    requirements=requirements,
                )
        except Exception as exc:
            await _mark_failed(task_id, str(exc)[:240], "interpreting")
            return

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return
        md_path, html_path = interpret_report.save_interpret_reports(
            task_id, interpret_result
        )

        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return
            if task.status in TERMINAL_STATUSES:
                return
            task.interpret_md_path = md_path
            task.interpret_html_path = html_path
            task.status = "generating_checklist"
            task.updated_at = utcnow()
            await session.commit()

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

    if need_checklist:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return
            if task.status in TERMINAL_STATUSES:
                return
            if task.status not in ("diagnosing", "paused"):
                task.status = "generating_checklist"
                task.updated_at = utcnow()
                await session.commit()

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        try:
            await wait_for_tender_parse_ready(task_id)
        except TenderParseBlockedError as exc:
            await _mark_failed(task_id, str(exc), "tender_parse")
            return

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        try:
            async with get_tracker(task_id).track("checklist.generate"):
                await ChecklistService(
                    agent=_build_checklist_agent()
                ).generate_for_task(task_id)
        except (
            ChecklistValidationError,
            ChecklistInputError,
            TenderParseBlockedError,
        ) as exc:
            await _handle_checklist_failure(task_id, exc)
            return
        except Exception as exc:
            await _handle_checklist_failure(task_id, exc)
            return

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

    # Caller (_run_checklist_lane) sets idle after the lane task clears.


async def _run_checklist_lane(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        await _run_interpret_and_checklist(task_id, stop_at_checklist=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await _handle_checklist_failure(task_id, exc)
    finally:
        ctrl.checklist_task = None
        if ctrl.resume_mode != "full":
            await _set_idle(task_id)


async def _run_bid_index_lane(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                return
            if await _bid_index_ready_in_session(session, task):
                return
            if not task.bid_file_id:
                await _mark_failed(task_id, "bid_file_missing", "bid_index")
                return
            task.status = "indexing_bid"
            task.updated_at = utcnow()
            await workspace.ensure_file_parse_enqueued(session, task.bid_file_id)
            await session.commit()
            await parse_scheduler.kick()

        await _wait_if_paused(task_id)
        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        try:
            await wait_for_bid_index_ready(task_id)
        except BidIndexBlockedError as exc:
            if str(exc) == "task_stopped":
                await _mark_stopped(task_id)
            else:
                await _mark_failed(task_id, str(exc), "bid_index")
            return

    except asyncio.CancelledError:
        raise
    finally:
        ctrl.bid_index_task = None
        if ctrl.resume_mode != "full":
            await _set_idle(task_id)


async def _run_diagnosis_lane(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        await _complete_from_diagnosis(task_id)
    finally:
        ctrl.diagnosis_task = None


async def _run_full(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        from app.services.task_readiness import compute_task_readiness

        await get_tracker(task_id).notify("start", status="running")
        readiness = await compute_task_readiness(task_id)
        if not readiness["checklist_ready"]:
            if not ctrl.lane_state()["checklist_lane_active"]:
                ctrl.checklist_task = asyncio.create_task(_run_checklist_lane(task_id))
        if readiness["bid_index_required"] and not readiness["bid_index_ready"]:
            if not ctrl.lane_state()["bid_index_lane_active"]:
                ctrl.bid_index_task = asyncio.create_task(_run_bid_index_lane(task_id))

        to_wait = [
            t
            for t in (ctrl.checklist_task, ctrl.bid_index_task)
            if t is not None and not t.done()
        ]
        if to_wait:
            await asyncio.gather(*to_wait, return_exceptions=True)

        readiness = await compute_task_readiness(task_id)
        if not readiness["checklist_ready"]:
            return

        await _complete_from_diagnosis(task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is not None and task.status not in TERMINAL_STATUSES:
                task.status = "failed"
                task.error_message = str(exc)
                task.finished_at = utcnow()
                task.updated_at = utcnow()
                await session.commit()
    finally:
        ctrl.full_task = None
        ctrl.done_event.set()


async def start_generate_checklist(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.lane_state()["checklist_lane_active"]:
        raise SchedulerConflict("task_lane_active")
    if ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")
        if task.current_checklist_generation_id is not None:
            raise SchedulerConflict("step_already_completed")

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.resume_mode = "checklist"
    ctrl.checklist_task = asyncio.create_task(_run_checklist_lane(task_id))


async def start_index_bid(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.lane_state()["bid_index_lane_active"]:
        raise SchedulerConflict("task_lane_active")
    if ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")
        if await _bid_index_ready_in_session(session, task):
            raise SchedulerConflict("step_already_completed")

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.resume_mode = "bid_index"
    ctrl.bid_index_task = asyncio.create_task(_run_bid_index_lane(task_id))


async def start_diagnose(task_id: str) -> None:
    from app.services.task_readiness import compute_task_readiness

    ctrl = _get_control(task_id)
    if ctrl.lane_state()["diagnosis_lane_active"] or ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")

    readiness = await compute_task_readiness(task_id)
    if not readiness["checklist_ready"]:
        raise SchedulerConflict("checklist_not_ready")
    if readiness["bid_index_required"] and not readiness["bid_index_ready"]:
        raise SchedulerConflict("bid_index_not_ready")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.resume_mode = "diagnosis"
    ctrl.diagnosis_task = asyncio.create_task(_run_diagnosis_lane(task_id))


async def start_run_full(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")
    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.resume_mode = "full"
    ctrl.done_event.clear()
    ctrl.full_task = asyncio.create_task(_run_full(task_id))


async def _mark_failed(
    task_id: str,
    error_message: str,
    failure_stage: str,
) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        if task.status in TERMINAL_STATUSES:
            return
        task.status = "failed"
        task.error_message = error_message
        task.failure_stage = failure_stage
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        await session.commit()


async def _run_diagnosis_phase(task_id: str) -> bool:
    """Run category-batch diagnosis. Returns False if stopped or failed."""
    checklist_report = await get_report(task_id)
    categories = checklist_report["categories"]

    has_file_items = any(
        (item.get("diagnosis_mode") or "file") != "offline"
        for category in categories
        for item in category["items"]
    )
    if has_file_items:
        try:
            await wait_for_bid_index_ready(task_id)
        except BidIndexBlockedError as exc:
            await _mark_failed(task_id, str(exc), "diagnosing")
            return False
    else:
        tracker = get_tracker(task_id)
        await tracker.notify("index.gate", status="skipped")

    await get_tracker(task_id).register_diagnosis_categories(categories)

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return False
        if task.status in TERMINAL_STATUSES:
            return False
        items_done = task.progress_done
        sort_order = items_done

    retrieval = build_retrieval_provider()
    engine = AgentOSBatchDiagnosisEngine(client=AgentOSClient())

    cumulative = 0
    for category in categories:
        category_items = category["items"]
        category_count = len(category_items)
        if cumulative + category_count <= items_done:
            cumulative += category_count
            continue

        await _wait_if_paused(task_id)
        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return False

        cat_id = category["id"]
        node_key = f"diagnosis.category.{cat_id}"
        async with get_tracker(task_id).track_node(node_key):
            offline_items, file_items = _split_items_by_diagnosis_mode(category_items)
            result_by_id: dict = {}
            for item in offline_items:
                result_by_id[item["id"]] = _offline_batch_result(item)

            if file_items:
                try:
                    retrieved_chunks = await retrieval.retrieve_for_category(
                        task_id=task_id,
                        category=category,
                        items=file_items,
                    )
                    batch_results = await engine.diagnose_category(
                        task_id=task_id,
                        category=category,
                        items=file_items,
                        retrieved_chunks=retrieved_chunks,
                    )
                    assert_batch_complete(file_items, batch_results)
                except (
                    AgentOSConfigError,
                    AgentOSError,
                    BatchDiagnosisResponseError,
                    ValueError,
                ) as exc:
                    await _mark_failed(task_id, str(exc)[:240], "diagnosing")
                    return False
                for batch_result in batch_results:
                    result_by_id[batch_result.checklist_item_id] = batch_result

            ordered_pairs = [
                (item, result_by_id[item["id"]]) for item in category_items
            ]

            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                if task is None:
                    return False
                if task.status in TERMINAL_STATUSES:
                    return False

                for item, batch_result in ordered_pairs:
                    session.add(
                        DiagnosisResult(
                            task_id=task_id,
                            checklist_item_id=batch_result.checklist_item_id,
                            content_title=item["title"],
                            description=batch_result.description
                            or item.get("requirement", ""),
                            result=batch_result.compliance,
                            compliance_status=batch_result.compliance,
                            consequence_tags=json.dumps(
                                batch_result.consequence_tags,
                                ensure_ascii=False,
                            ),
                            evidence=batch_result.evidence,
                            suggestion=batch_result.suggestion,
                            response_content=batch_result.response_content,
                            sort_order=sort_order,
                        )
                    )
                    sort_order += 1

                task.progress_done = sort_order
                task.updated_at = utcnow()
                await session.commit()

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return False

    return True


async def _complete_from_diagnosis(task_id: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        if task.status in TERMINAL_STATUSES:
            return
        if task.status != "paused":
            task.status = "diagnosing"
            task.error_message = None
            task.failure_stage = None
            task.finished_at = None
            task.updated_at = utcnow()
            await session.commit()

    if not await _run_diagnosis_phase(task_id):
        return

    if _should_stop(task_id):
        await _mark_stopped(task_id)
        return

    async with get_tracker(task_id).track("report.generate"):
        md_path, docx_path = await report.generate_and_save_reports(task_id)

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        if task.status in TERMINAL_STATUSES:
            return
        if _should_stop(task_id):
            task.status = "stopped"
        else:
            task.status = "completed"
            task.report_md_path = md_path
            task.report_docx_path = docx_path
        task.finished_at = utcnow()
        task.updated_at = utcnow()
        await session.commit()

    if not _should_stop(task_id):
        tracker = get_tracker(task_id)
        await tracker.notify("start", status="completed")
        await tracker.notify("end", status="completed")


async def _run(task_id: str) -> None:
    """Deprecated alias kept for internal references."""
    await _run_full(task_id)
