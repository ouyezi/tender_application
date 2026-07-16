from __future__ import annotations

import asyncio
import io

import pytest
from httpx import AsyncClient

from app.services import scheduler


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


async def _create_task(client: AsyncClient) -> dict:
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
    return r.json()


@pytest.mark.asyncio
async def test_scheduler_runs_to_completion(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "completed"

    r = await client.get(f"/api/tasks/{task_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["progress_done"] == 3
    assert detail["progress_total"] == 3
    assert len(detail["results"]) == 3


@pytest.mark.asyncio
async def test_pause_resume_completes(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]

    paused = False
    for _ in range(40):
        r = await client.post(f"/api/tasks/{task_id}/pause")
        if r.status_code == 200:
            data = r.json()
            assert data["status"] == "paused"
            assert data["progress_done"] < 3
            paused = True
            break
        if r.status_code == 409:
            detail = (await client.get(f"/api/tasks/{task_id}")).json()
            if detail["status"] == "completed":
                pytest.skip("task completed before pause; timing too fast")
        await asyncio.sleep(0.02)

    assert paused, "failed to pause task while running"

    r = await client.post(f"/api/tasks/{task_id}/resume")
    assert r.status_code == 200
    assert r.json()["status"] == "running"

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "completed"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["progress_done"] == 3
    assert len(detail["results"]) == 3


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
async def test_pause_on_completed_conflict(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "completed"

    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 409
