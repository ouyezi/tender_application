import asyncio
import io

import pytest


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_draft(client):
    await client.post(
        "/api/configs",
        json={
            "title": "资质",
            "technique": "查",
            "content_mode": "description",
            "content_text": "资质",
            "importance": "high",
        },
    )
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={}, files=files)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_diagnose_requires_checklist(client):
    task_id = await _create_draft(client)
    r = await client.post(f"/api/tasks/{task_id}/actions/diagnose")
    assert r.status_code == 409
    assert "checklist_not_ready" in r.text


@pytest.mark.asyncio
async def test_parallel_actions_both_accepted(client):
    task_id = await _create_draft(client)
    r1 = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    r2 = await client.post(f"/api/tasks/{task_id}/actions/index-bid")
    assert r1.status_code == 202
    assert r2.status_code == 202


@pytest.mark.asyncio
async def test_duplicate_checklist_action_conflict(client):
    task_id = await _create_draft(client)
    r1 = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    assert r1.status_code == 202
    await asyncio.sleep(0.05)
    r2 = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    assert r2.status_code == 409
    assert "task_lane_active" in r2.text or "step_already_completed" in r2.text
