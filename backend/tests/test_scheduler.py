from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from httpx import AsyncClient

from app import config, db
from app.models import DiagnosisTask, WorkspaceFile
from app.engine.base import InterpretationResult
from app.engine.batch_diagnosis_mock import MockBatchDiagnosisEngine
from app.engine.checklist_agent_os import AgentOSChecklistAgent
from tests.fake_checklist_invoke import make_fake_checklist_invoke
from app.services import scheduler


@pytest.fixture(autouse=True)
def _stub_batch_diagnosis_for_scheduler_tests(monkeypatch):
    """Keep scheduler integration tests offline: mock engine + skip index gate."""

    class _StubEngine(MockBatchDiagnosisEngine):
        def __init__(self, *a, **k):
            del a, k
            super().__init__(delay_seconds=config.MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS)

    async def _noop_wait(task_id, timeout=None):
        del task_id, timeout

    monkeypatch.setattr(scheduler, "AgentOSBatchDiagnosisEngine", _StubEngine)
    monkeypatch.setattr(scheduler, "wait_for_bid_index_ready", _noop_wait)


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _seed_configs(client: AsyncClient, n: int = 3) -> None:
    for i in range(n):
        r = await client.post(
            "/api/configs",
            json={
                "title": f"检查项{i + 1}",
                "technique": "对照",
                "content_mode": "description",
                "content_text": f"内容{i + 1}",
                "importance": "medium",
            },
        )
        assert r.status_code == 201


async def _create_task(client: AsyncClient, *, run_full: bool = True) -> dict:
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": (
            "bid.docx",
            io.BytesIO(b"PK fake"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    r = await client.post(
        "/api/tasks",
        data={"background": "bg", "requirements": "req"},
        files=files,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "draft"
    if run_full:
        await _start_full(client, body["id"])
    return body


async def _start_full(client: AsyncClient, task_id: str) -> None:
    r = await client.post(f"/api/tasks/{task_id}/actions/run-full")
    assert r.status_code == 202


async def _wait_for_checklist_items(client: AsyncClient, task_id: str) -> int:
    for _ in range(200):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail.get("progress_total", 0) > 0:
            return detail["progress_total"]
        await asyncio.sleep(0.02)
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    return detail.get("progress_total", 0)


@pytest.mark.asyncio
async def test_scheduler_runs_to_completion(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"

    r = await client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["current_checklist_generation_id"] is not None
    assert detail["progress_done"] == detail["progress_total"]
    assert detail["progress_total"] > 0
    assert len(detail["results"]) == detail["progress_total"]
    assert all(result["checklist_item_id"] for result in detail["results"])
    assert all(result["compliance_status"] for result in detail["results"])
    assert detail["interpret_md_path"]
    assert not detail.get("interpret_html_path")
    interpret_md = Path(detail["interpret_md_path"]).read_text(encoding="utf-8")
    assert "stub interpret" in interpret_md
    assert "（Mock）" not in interpret_md


@pytest.mark.asyncio
async def test_progress_total_matches_checklist_items(client):
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]

    expected_total = await _wait_for_checklist_items(client, task_id)
    assert expected_total > 0

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["progress_total"] == expected_total
    assert detail["progress_done"] == expected_total


@pytest.mark.asyncio
async def test_pause_resume_completes(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]
    total = await _wait_for_checklist_items(client, task_id)
    assert total > 0

    paused = False
    for _ in range(80):
        r = await client.post(f"/api/tasks/{task_id}/pause")
        if r.status_code == 200:
            data = r.json()
            assert data["status"] == "paused"
            assert data["progress_done"] < total
            paused = True
            break
        if r.status_code == 409:
            detail = (await client.get(f"/api/tasks/{task_id}")).json()
            if detail["status"] == "completed":
                pytest.skip("task completed before pause; timing too fast")
        await asyncio.sleep(0.02)

    assert paused, "failed to pause task while diagnosing"

    r = await client.post(f"/api/tasks/{task_id}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "diagnosing"

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["progress_done"] == total
    assert len(detail["results"]) == total


@pytest.mark.asyncio
async def test_stop_then_resume_conflict(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]

    r = await client.post(f"/api/tasks/{task_id}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"

    r2 = await client.post(f"/api/tasks/{task_id}/resume")
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_resume_preserves_progress_no_duplicates(client, monkeypatch):
    monkeypatch.setattr("app.config.MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS", 0.3)
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]
    total = await _wait_for_checklist_items(client, task_id)
    assert total > 1

    paused = False
    for _ in range(120):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["progress_done"] >= 1 and detail["status"] == "diagnosing":
            r = await client.post(f"/api/tasks/{task_id}/pause")
            if r.status_code == 200:
                paused = True
                break
            if r.status_code == 409 and detail["status"] == "completed":
                pytest.skip("task completed before pause; timing too fast")
        await asyncio.sleep(0.02)

    assert paused, "failed to pause task after at least one item completed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    item_ids_before = [r["checklist_item_id"] for r in detail["results"]]
    assert len(item_ids_before) == detail["progress_done"]

    r = await client.post(f"/api/tasks/{task_id}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "diagnosing"

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["progress_done"] == total
    assert len(detail["results"]) == total

    item_ids_after = [r["checklist_item_id"] for r in detail["results"]]
    assert len(item_ids_after) == len(set(item_ids_after))
    assert item_ids_after[: len(item_ids_before)] == item_ids_before


@pytest.mark.asyncio
async def test_stop_preserves_partial_results(client, monkeypatch):
    monkeypatch.setattr("app.config.MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS", 0.3)
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]
    total = await _wait_for_checklist_items(client, task_id)
    assert total > 1

    stopped = False
    for _ in range(120):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["progress_done"] >= 1 and detail["status"] == "diagnosing":
            r = await client.post(f"/api/tasks/{task_id}/stop")
            if r.status_code == 200:
                stopped = True
                break
        if detail["status"] in ("stopped", "completed", "failed"):
            break
        await asyncio.sleep(0.02)

    assert stopped, "failed to stop task after at least one item completed"

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert 0 < detail["progress_done"] < detail["progress_total"]
    assert len(detail["results"]) == detail["progress_done"]


@pytest.mark.asyncio
async def test_pause_on_completed_conflict(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"

    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_interpretation_failure_fails_task(client, monkeypatch):
    class Boom:
        async def interpret(self, **kwargs):
            raise RuntimeError("interpret boom")

    monkeypatch.setattr(
        scheduler,
        "_build_interpretation_agent",
        lambda: Boom(),
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    status = await scheduler.wait_for_terminal(body["id"], timeout=10)
    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{body['id']}")).json()
    assert detail["results"] == []
    assert detail.get("failure_stage") == "interpreting"
    assert "interpret boom" in (detail.get("error_message") or "")
    assert detail.get("interpret_markdown", "") == ""
    r = await client.get(f"/api/tasks/{body['id']}/report.docx")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tender_content_failure_fails_interpreting_stage(client, monkeypatch):
    from app.services.tender_content import TenderContentError

    class BoomProvider:
        async def wait_for_markdown(self, task_id, file_id, *, stop_requested):
            del task_id, file_id, stop_requested
            raise TenderContentError("file-1: parse-failed")

    monkeypatch.setattr(
        scheduler,
        "_build_tender_content_provider",
        lambda: BoomProvider(),
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    status = await scheduler.wait_for_terminal(body["id"], timeout=10)
    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{body['id']}")).json()
    assert detail.get("failure_stage") == "interpreting"
    assert "parse-failed" in (detail.get("error_message") or "")


@pytest.mark.asyncio
async def test_can_pause_while_interpreting(client, monkeypatch):
    gate = asyncio.Event()

    class SlowAgent:
        async def interpret(self, **kwargs):
            await gate.wait()
            return InterpretationResult(markdown="# x\n")

    monkeypatch.setattr(
        scheduler,
        "_build_interpretation_agent",
        lambda: SlowAgent(),
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    saw_interpreting = False
    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "interpreting":
            saw_interpreting = True
            break
        await asyncio.sleep(0.02)
    assert saw_interpreting, "never saw interpreting status"

    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    gate.set()
    await client.post(f"/api/tasks/{task_id}/resume")
    await scheduler.wait_for_terminal(task_id, timeout=10)


@pytest.mark.asyncio
async def test_stop_during_interpreting(client, monkeypatch):
    gate = asyncio.Event()

    class SlowAgent:
        async def interpret(self, **kwargs):
            await gate.wait()
            return InterpretationResult(markdown="# x\n")

    monkeypatch.setattr(
        scheduler,
        "_build_interpretation_agent",
        lambda: SlowAgent(),
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    saw_interpreting = False
    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "interpreting":
            saw_interpreting = True
            break
        await asyncio.sleep(0.02)
    assert saw_interpreting, "never saw interpreting status"

    r = await client.post(f"/api/tasks/{task_id}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"

    gate.set()
    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"


@pytest.mark.asyncio
async def test_parse_failed_marks_failed_with_failure_stage(client, monkeypatch):
    # Checklist-stage wait_for_tender_parse_ready under conftest interpret stub;
    # not the real TenderContentProvider wait during interpretation.
    from app.services.checklist_service import TenderParseBlockedError

    async def blocked_wait(task_id, timeout=300.0):
        del task_id, timeout
        raise TenderParseBlockedError("tender_parse_failed")

    monkeypatch.setattr(
        "app.services.scheduler.wait_for_tender_parse_ready",
        blocked_wait,
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "failed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["failure_stage"] == "tender_parse"
    assert "tender_parse_failed" in (detail.get("error_message") or "")


@pytest.mark.asyncio
async def test_can_pause_while_generating_checklist(client, monkeypatch):
    gate = asyncio.Event()

    class BlockingChecklistAgent:
        async def generate(self, *, task_id, context):
            await gate.wait()
            agent = AgentOSChecklistAgent(invoke_app=make_fake_checklist_invoke())
            return await agent.generate(task_id=task_id, context=context)

    monkeypatch.setattr(
        "app.services.scheduler.AgentOSChecklistAgent",
        BlockingChecklistAgent,
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    saw_generating = False
    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "generating_checklist":
            saw_generating = True
            break
        await asyncio.sleep(0.02)
    assert saw_generating, "never saw generating_checklist status"

    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    gate.set()
    await client.post(f"/api/tasks/{task_id}/resume")
    await scheduler.wait_for_terminal(task_id, timeout=10)


@pytest.mark.asyncio
async def test_stop_during_generating_checklist(client, monkeypatch):
    gate = asyncio.Event()

    class BlockingChecklistAgent:
        async def generate(self, *, task_id, context):
            await gate.wait()
            agent = AgentOSChecklistAgent(invoke_app=make_fake_checklist_invoke())
            return await agent.generate(task_id=task_id, context=context)

    monkeypatch.setattr(
        "app.services.scheduler.AgentOSChecklistAgent",
        BlockingChecklistAgent,
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    saw_generating = False
    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "generating_checklist":
            saw_generating = True
            break
        await asyncio.sleep(0.02)
    assert saw_generating, "never saw generating_checklist status"

    r = await client.post(f"/api/tasks/{task_id}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"

    gate.set()
    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"


@pytest.mark.asyncio
async def test_parse_failed_via_workspace_file(client, monkeypatch):
    # Checklist-stage wait_for_tender_parse_ready under conftest interpret stub;
    # not the real TenderContentProvider wait during interpretation.
    from app.services.checklist_service import TenderParseBlockedError

    gate = asyncio.Event()

    class SlowAgent:
        async def interpret(self, **kwargs):
            await gate.wait()
            return InterpretationResult(markdown="# x\n")

    async def blocked_wait(task_id, timeout=300.0):
        del timeout
        async with db.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            workspace_file = await session.get(WorkspaceFile, task.tender_file_id)
            workspace_file.parse_status = "failed"
            await session.commit()
        raise TenderParseBlockedError("tender_parse_failed")

    monkeypatch.setattr(
        scheduler,
        "_build_interpretation_agent",
        lambda: SlowAgent(),
    )
    monkeypatch.setattr(
        "app.services.scheduler.wait_for_tender_parse_ready",
        blocked_wait,
    )
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    saw_interpreting = False
    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "interpreting":
            saw_interpreting = True
            gate.set()
            break
        await asyncio.sleep(0.02)
    assert saw_interpreting, "never saw interpreting status"

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["failure_stage"] == "tender_parse"


def test_offline_batch_result_parses_markdown_consequence_tags():
    from app.services.scheduler import _offline_batch_result

    item = {
        "id": "item-1",
        "title": "签章",
        "requirement": "需签章",
        "consequence_rules": "[bid_unusable]\n否决",
        "diagnosis_mode": "offline",
    }
    result = _offline_batch_result(item)
    assert result.compliance == "manual_required"
    assert "bid_unusable" in result.consequence_tags


def test_split_and_offline_result():
    from app.services.scheduler import (
        _split_items_by_diagnosis_mode,
        _offline_batch_result,
    )

    offline, file_items = _split_items_by_diagnosis_mode(
        [
            {
                "id": "a",
                "diagnosis_mode": "offline",
                "title": "装订",
                "requirement": "胶装",
            },
            {"id": "b", "diagnosis_mode": "file", "title": "执照"},
            {"id": "c", "title": "缺省"},
        ]
    )
    assert [i["id"] for i in offline] == ["a"]
    assert [i["id"] for i in file_items] == ["b", "c"]
    result = _offline_batch_result(offline[0])
    assert result.compliance == "manual_required"
    assert result.consequence_tags == []
    assert result.evidence == "未检索文件（线下核验项）"
    assert result.suggestion == (
        "该项属于打印/装订/密封等线下要求，需人工核验纸质或现场材料，系统不进行文件诊断"
    )
    assert result.description == "胶装"


@pytest.mark.asyncio
async def test_run_diagnosis_phase_mixed_modes(monkeypatch, client):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.engine.base import BatchItemResult
    from app.models import DiagnosisResult

    calls = {"retrieve": 0, "diagnose": 0}

    class R:
        async def retrieve_for_category(self, **kwargs):
            calls["retrieve"] += 1
            assert [i["id"] for i in kwargs["items"]] == ["file-1"]
            return []

    class E:
        def __init__(self, *a, **k):
            pass

        async def diagnose_category(self, **kwargs):
            calls["diagnose"] += 1
            item = kwargs["items"][0]
            return [
                BatchItemResult(
                    checklist_item_id=item["id"],
                    compliance="satisfied",
                    consequence_tags=[],
                    evidence="e",
                    suggestion="s",
                    description="d",
                )
            ]

    monkeypatch.setattr(scheduler, "build_retrieval_provider", lambda: R())
    monkeypatch.setattr(scheduler, "AgentOSBatchDiagnosisEngine", E)

    async def fake_report(task_id):
        del task_id
        return {
            "categories": [
                {
                    "id": "c1",
                    "name": "c",
                    "items": [
                        {
                            "id": "offline-1",
                            "title": "密封",
                            "requirement": "密封",
                            "diagnosis_mode": "offline",
                            "consequence_rules": {},
                        },
                        {
                            "id": "file-1",
                            "title": "执照",
                            "requirement": "执照",
                            "diagnosis_mode": "file",
                            "consequence_rules": {},
                        },
                    ],
                }
            ]
        }

    monkeypatch.setattr(scheduler, "get_report", fake_report)

    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="task-offline-mix",
                tender_filename="t.pdf",
                tender_path="t.pdf",
                bid_filename="b.docx",
                bid_path="b.docx",
                status="diagnosing",
                progress_done=0,
                progress_total=2,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    assert await scheduler._run_diagnosis_phase("task-offline-mix") is True
    assert calls["retrieve"] == 1
    assert calls["diagnose"] == 1

    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(DiagnosisResult).where(
                    DiagnosisResult.task_id == "task-offline-mix"
                )
            )
        ).scalars().all()
    statuses = {r.checklist_item_id: r.compliance_status for r in rows}
    assert statuses["offline-1"] == "manual_required"
    assert statuses["file-1"] == "satisfied"
    assert [r.checklist_item_id for r in rows] == ["offline-1", "file-1"]


@pytest.mark.asyncio
async def test_diagnosis_fails_when_bid_index_wait_blocked(monkeypatch, client):
    from datetime import datetime, timezone

    from app.models import DiagnosisTask
    from app.services import scheduler
    from app.services.bid_index_wait import BidIndexBlockedError

    async def boom(task_id, timeout=None):
        del task_id, timeout
        raise BidIndexBlockedError("bid_index_timeout")

    wait_calls = {"n": 0}

    async def tracked_wait(task_id, timeout=None):
        wait_calls["n"] += 1
        return await boom(task_id, timeout)

    monkeypatch.setattr(scheduler, "wait_for_bid_index_ready", tracked_wait)

    async def fake_report(task_id):
        del task_id
        return {
            "categories": [
                {
                    "id": "c1",
                    "name": "c",
                    "items": [
                        {
                            "id": "file-1",
                            "title": "执照",
                            "requirement": "执照",
                            "diagnosis_mode": "file",
                            "consequence_rules": {},
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(scheduler, "get_report", fake_report)

    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="task-idx-gate",
                tender_filename="t.pdf",
                tender_path="t.pdf",
                bid_filename="b.docx",
                bid_path="b.docx",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    assert await scheduler._run_diagnosis_phase("task-idx-gate") is False
    assert wait_calls["n"] == 1
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "task-idx-gate")
        assert task is not None
        assert task.status == "failed"
        assert task.failure_stage == "diagnosing"
        assert "bid_index_timeout" in (task.error_message or "")


@pytest.mark.asyncio
async def test_diagnosis_skips_index_wait_when_all_offline(monkeypatch, client):
    from datetime import datetime, timezone

    from app.models import DiagnosisTask
    from app.services import scheduler

    wait_calls = {"n": 0}

    async def tracked_wait(task_id, timeout=None):
        del task_id, timeout
        wait_calls["n"] += 1

    monkeypatch.setattr(scheduler, "wait_for_bid_index_ready", tracked_wait)

    class E:
        def __init__(self, *a, **k):
            pass

        async def diagnose_category(self, **kwargs):
            raise AssertionError("engine should not run for all-offline")

    monkeypatch.setattr(scheduler, "AgentOSBatchDiagnosisEngine", E)

    async def fake_report(task_id):
        del task_id
        return {
            "categories": [
                {
                    "id": "c1",
                    "name": "c",
                    "items": [
                        {
                            "id": "offline-1",
                            "title": "密封",
                            "requirement": "密封",
                            "diagnosis_mode": "offline",
                            "consequence_rules": {},
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(scheduler, "get_report", fake_report)

    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="task-all-offline",
                tender_filename="t.pdf",
                tender_path="t.pdf",
                bid_filename="b.docx",
                bid_path="b.docx",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    assert await scheduler._run_diagnosis_phase("task-all-offline") is True
    assert wait_calls["n"] == 0


@pytest.mark.asyncio
async def test_start_generate_checklist_from_draft(client):
    await _seed_configs(client, 1)
    body = await _create_task(client, run_full=False)
    task_id = body["id"]

    r = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    assert r.status_code == 202

    status = await scheduler.wait_for_idle(task_id, timeout=60)
    assert status in ("draft", "failed")

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["current_checklist_generation_id"] is not None
    assert detail["interpret_md_path"]
    assert detail["status"] == "draft"


@pytest.mark.asyncio
async def test_pause_during_generating_checklist(client):
    await _seed_configs(client, 2)
    body = await _create_task(client, run_full=False)
    task_id = body["id"]
    await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")

    for _ in range(50):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["status"] in ("generating_checklist", "interpreting"):
            break
        await asyncio.sleep(0.02)

    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"
