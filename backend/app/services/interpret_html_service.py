from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

from app.config import REPORT_DIR
from app.engine.interpret_html_agent_os import AgentOSInterpretHtmlAgent
from app.interpret_html_schema import InterpretHtmlReportData
from app.models import DiagnosisTask, utcnow
from app.services import artifact
from app.services.agent_os import AgentOSClient, load_settings
from app.templates.interpret_html_report import render_interpret_html_report
from app import db as database

logger = logging.getLogger(__name__)

_active: set[str] = set()
_errors: dict[str, str] = {}


class InterpretHtmlConflict(Exception):
    pass


def is_lane_active(task_id: str) -> bool:
    return task_id in _active


def get_error(task_id: str) -> str | None:
    return _errors.get(task_id)


def _set_lane_active_for_test(task_id: str, active: bool) -> None:
    if active:
        _active.add(task_id)
    else:
        _active.discard(task_id)


def _read_interpret_md(task_id: str) -> str:
    md_path = REPORT_DIR / task_id / "interpret.md"
    if not md_path.is_file():
        raise FileNotFoundError("interpret markdown not found")
    return md_path.read_text(encoding="utf-8")


def _build_agent() -> AgentOSInterpretHtmlAgent:
    settings = load_settings()
    html_settings = replace(
        settings,
        timeout_seconds=settings.interpret_html_invoke_timeout_seconds,
    )
    return AgentOSInterpretHtmlAgent(client=AgentOSClient(settings=html_settings))


async def _generate_data(task_id: str, interpret_report: str) -> InterpretHtmlReportData:
    agent = _build_agent()
    return await agent.generate(task_id=task_id, interpret_report=interpret_report)


async def _persist_html_path(task_id: str, html_path: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            return
        task.interpret_html_path = html_path
        task.updated_at = utcnow()
        await session.commit()


async def start(task_id: str) -> None:
    if task_id in _active:
        raise InterpretHtmlConflict("interpret_html_lane_active")
    _active.add(task_id)
    _errors.pop(task_id, None)
    asyncio.create_task(_run(task_id))


async def _run(task_id: str) -> None:
    try:
        md = _read_interpret_md(task_id)
        data = await _generate_data(task_id, md)
        html = render_interpret_html_report(data, task_id=task_id)
        html_path = REPORT_DIR / task_id / "interpret.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")
        artifact.sync_to_artifact_report(task_id, html_path)
        await _persist_html_path(task_id, str(html_path))
        _errors.pop(task_id, None)
        logger.info("Interpret HTML report generated task_id=%s path=%s", task_id, html_path)
    except Exception as exc:
        _errors[task_id] = str(exc)[:240]
        logger.exception("Interpret HTML generation failed task_id=%s", task_id)
    finally:
        _active.discard(task_id)
