from __future__ import annotations

import io

import pytest
from httpx import AsyncClient

from app.services import artifact, scheduler


def _pdf_bytes():
    return b"%PDF-1.4 fake"


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
async def test_delete_task_not_found(client):
    r = await client.delete("/api/tasks/T-MISSING-001")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_completed_task_removes_db_and_disk(client):
    body = await _create_task(client)
    task_id = body["id"]

    r_stop = await client.post(f"/api/tasks/{task_id}/stop")
    assert r_stop.status_code == 200
    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"

    root = artifact.artifact_root(task_id)
    assert root.is_dir()

    r = await client.delete(f"/api/tasks/{task_id}")
    assert r.status_code == 204

    r_get = await client.get(f"/api/tasks/{task_id}")
    assert r_get.status_code == 404
    assert not root.is_dir()


@pytest.mark.asyncio
async def test_delete_running_task(client):
    body = await _create_task(client)
    task_id = body["id"]

    r = await client.delete(f"/api/tasks/{task_id}")
    assert r.status_code == 204

    r_get = await client.get(f"/api/tasks/{task_id}")
    assert r_get.status_code == 404
