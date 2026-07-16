from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app import db as database
from app.config import MOCK_ITEM_DELAY_SECONDS, MOCK_INTERPRET_DELAY_SECONDS
from app.engine.interpretation_mock import MockInterpretationAgent
from app.engine.mock import MockEngine
from app.models import DiagnosisResult, DiagnosisTask, utcnow
from app.services import interpret_report, report


class SchedulerConflict(Exception):
    """Raised when pause/resume/stop is invalid for the current task status."""


TERMINAL_STATUSES = frozenset({"completed", "stopped", "failed"})
STOPPABLE_STATUSES = frozenset({"interpreting", "diagnosing", "running", "paused"})


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


def reset_for_tests() -> None:
    """Clear in-memory scheduler state between tests."""
    for ctrl in list(_controls.values()):
        if ctrl.bg_task is not None and not ctrl.bg_task.done():
            ctrl.stop_requested = True
            ctrl.pause_event.set()
            ctrl.bg_task.cancel()
    _controls.clear()


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

    # Prefer letting the runner mark stopped; if idle, do it here.
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

    # Wait for the cooperative loop to observe the stop flag.
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

            snapshot: list[dict[str, Any]] = json.loads(task.config_snapshot or "[]")
            start_idx = task.progress_done
            tender_path = task.tender_path
            bid_path = task.bid_path
            background = task.background or ""

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
                task.status = "diagnosing"
                task.updated_at = utcnow()
                await session.commit()

            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

        engine = MockEngine(delay_seconds=MOCK_ITEM_DELAY_SECONDS)
        documents = {"tender_path": tender_path, "bid_path": bid_path}

        for idx, item in enumerate(snapshot):
            if idx < start_idx:
                continue

            await _wait_if_paused(task_id)
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            result = await engine.diagnose_item(task_id, item, documents)

            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                if task is None:
                    return
                if task.status in TERMINAL_STATUSES:
                    return

                session.add(
                    DiagnosisResult(
                        task_id=task_id,
                        config_id=result.config_id,
                        content_title=result.content_title,
                        description=result.description,
                        result=result.result,
                        evidence=result.evidence,
                        suggestion=result.suggestion,
                        sort_order=idx,
                    )
                )
                task.progress_done = idx + 1
                task.updated_at = utcnow()
                await session.commit()

            if _should_stop(task_id):
                await _mark_stopped(task_id)
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
