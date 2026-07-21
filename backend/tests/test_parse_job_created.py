import asyncio
import io

import pytest
from sqlalchemy import select

from app import db as database
from app.models import DiagnosisTask, ParseJob, WorkspaceFile


def _pdf_bytes():
    return b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_parse_jobs_created_for_generate_checklist(client):
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
    task_id = r.json()["id"]
    await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")

    saw_job = False
    saw_succeeded = False
    for _ in range(200):
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            jobs = (
                await session.execute(select(ParseJob).where(ParseJob.task_id == task_id))
            ).scalars().all()
            wf = await session.get(WorkspaceFile, task.tender_file_id)
            if jobs:
                saw_job = True
            if wf and wf.parse_status == "succeeded":
                saw_succeeded = True
                break
        await asyncio.sleep(0.05)

    assert saw_job, "ParseJob was never created for generate-checklist"
    assert saw_succeeded, f"tender parse never succeeded, wf={wf.parse_status if wf else None}"

    status = await __import__("app.services.scheduler", fromlist=["wait_for_idle"]).wait_for_idle(
        task_id, timeout=15
    )
    assert status in ("draft", "failed")
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        assert task.current_checklist_generation_id is not None
