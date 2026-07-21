import io

import pytest

from app.services.task_readiness import compute_task_readiness


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_draft(client):
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={}, files=files)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_readiness_for_fresh_draft(client):
    task_id = await _create_draft(client)
    readiness = await compute_task_readiness(task_id)
    assert readiness["checklist_ready"] is False
    assert readiness["bid_index_ready"] is False
    assert readiness["bid_index_required"] is True
    assert readiness["diagnosis_ready"] is False
    assert readiness["checklist_lane_active"] is False
    assert readiness["bid_index_lane_active"] is False
    assert readiness["full_run_active"] is False
