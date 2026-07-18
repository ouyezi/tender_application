from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

from app import db as database
from app.config import (
    MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS,
    MOCK_INTERPRET_DELAY_SECONDS,
)
from app.engine.batch_diagnosis_mock import MockBatchDiagnosisEngine
from app.engine.checklist_agent_os import AgentOSChecklistAgent
from app.engine.interpretation_mock import MockInterpretationAgent
from app.engine.retrieval_mock import MockRetrievalProvider
from app.engine.retrieval_workspace import WorkspaceRetrievalProvider
from app.models import DiagnosisResult, DiagnosisTask, WorkspaceFile, utcnow
from app.services import interpret_report, report
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


class SchedulerConflict(Exception):
    """Raised when pause/resume/stop is invalid for the current task status."""


def build_retrieval_provider():
    from app import config

    if config.RETRIEVAL_PROVIDER == "workspace":
        return WorkspaceRetrievalProvider()
    return MockRetrievalProvider()


TERMINAL_STATUSES = frozenset({"completed", "stopped", "failed"})
STOPPABLE_STATUSES = frozenset(
    {"interpreting", "generating_checklist", "diagnosing", "running", "paused"}
)


@dataclass
class _TaskControl:
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: bool = False
    bg_task: Optional[asyncio.Task] = None
    done_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        # Set = not paused (may proceed); clear = paused (wait).
        if not self.pause_event.is_set():
            self.pause_event.set()


_controls: dict[str, _TaskControl] = {}


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
    if ctrl.bg_task is not None and not ctrl.bg_task.done():
        ctrl.bg_task.cancel()


async def reset_for_tests() -> None:
    """Clear in-memory scheduler state between tests."""
    tasks = []
    for ctrl in list(_controls.values()):
        if ctrl.bg_task is not None and not ctrl.bg_task.done():
            ctrl.stop_requested = True
            ctrl.pause_event.set()
            ctrl.bg_task.cancel()
            tasks.append(ctrl.bg_task)
    _controls.clear()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


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
    """Fire-and-forget: spawn background runner without awaiting the full run."""
    ctrl = _get_control(task_id)
    if ctrl.bg_task is not None and not ctrl.bg_task.done():
        return
    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.done_event.clear()
    ctrl.bg_task = asyncio.create_task(_run(task_id))


async def retry_checklist(task_id: str) -> DiagnosisTask:
    ctrl = _get_control(task_id)
    if ctrl.bg_task is not None and not ctrl.bg_task.done():
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
    ctrl.bg_task = asyncio.create_task(_run_checklist_retry(task_id))
    return prepared


async def _run_checklist_retry(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        await ChecklistService(agent=AgentOSChecklistAgent()).generate_for_task(task_id)

        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return
            if task.current_checklist_generation_id is None:
                raise RuntimeError("checklist_generation_missing")

        await _complete_from_diagnosis(task_id)
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
        if task.status != "diagnosing":
            raise SchedulerConflict(f"cannot pause task in status {task.status}")
        task.status = "paused"
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        paused = task

    _get_control(task_id).pause_event.clear()
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
        task.status = "diagnosing"
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        resumed = task

    ctrl.pause_event.set()

    if ctrl.bg_task is None or ctrl.bg_task.done():
        ctrl.done_event.clear()
        ctrl.bg_task = asyncio.create_task(_run(task_id))
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

    if ctrl.bg_task is None or ctrl.bg_task.done():
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
    """Run category-batch diagnosis. Returns False if stopped."""
    checklist_report = await get_report(task_id)
    categories = checklist_report["categories"]

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return False
        if task.status in TERMINAL_STATUSES:
            return False
        items_done = task.progress_done
        sort_order = items_done

    retrieval = build_retrieval_provider()
    engine = MockBatchDiagnosisEngine(
        delay_seconds=MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS
    )

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

        retrieved_chunks = await retrieval.retrieve_for_category(
            task_id=task_id,
            category=category,
            items=category_items,
        )
        batch_results = await engine.diagnose_category(
            task_id=task_id,
            category=category,
            items=category_items,
            retrieved_chunks=retrieved_chunks,
        )
        assert_batch_complete(category_items, batch_results)

        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return False
            if task.status in TERMINAL_STATUSES:
                return False

            for item, batch_result in zip(category_items, batch_results):
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


async def _run(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return
            if task.status in TERMINAL_STATUSES:
                return

            need_interpret = not task.interpret_md_path
            if need_interpret and task.status not in ("diagnosing", "paused"):
                task.status = "interpreting"
                task.updated_at = utcnow()
                await session.commit()

            tender_path = task.tender_path
            background = task.background or ""
            need_checklist = task.current_checklist_generation_id is None

        if need_interpret:
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            agent = MockInterpretationAgent(delay_seconds=MOCK_INTERPRET_DELAY_SECONDS)
            interpret_result = await agent.interpret(
                task_id=task_id,
                tender_path=tender_path,
                background=background,
            )
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
                await ChecklistService(agent=AgentOSChecklistAgent()).generate_for_task(
                    task_id
                )
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
        ctrl.done_event.set()
