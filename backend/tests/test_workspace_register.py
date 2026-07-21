import io

import pytest
from sqlalchemy import select

from app.models import ParseJob, WorkspaceFile


def _pdf_bytes():
    return b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_register_task_documents_without_enqueue_parse(client):
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={"background": "bg"}, files=files)
    assert r.status_code == 201
    task_id = r.json()["id"]
    assert r.json()["status"] == "draft"

    from app.db import SessionLocal

    async with SessionLocal() as session:
        from app.models import DiagnosisTask

        task = await session.get(DiagnosisTask, task_id)
        assert task.tender_file_id is not None
        assert task.bid_file_id is not None
        jobs = (
            await session.execute(select(ParseJob).where(ParseJob.task_id == task_id))
        ).scalars().all()
        assert jobs == []

        wfs = (
            await session.execute(
                select(WorkspaceFile).where(WorkspaceFile.task_id == task_id)
            )
        ).scalars().all()
        assert len(wfs) == 2
        assert all(wf.parse_status == "pending" for wf in wfs)
