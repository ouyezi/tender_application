from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import DiagnosisTask, ParseJob, WorkspaceFile
from app.services import artifact, workspace


@pytest.mark.asyncio
async def test_register_task_documents(tmp_path, monkeypatch, client):
    from app import db as database

    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(workspace, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "uploads").mkdir(exist_ok=True)

    task_id = "T-REG-001"
    root = artifact.ensure_artifact_dirs(task_id)
    tender = root / "tender.docx"
    bid = root / "bid.docx"
    tender.write_bytes(b"t")
    bid.write_bytes(b"b")

    async with database.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="tender.docx",
                tender_path=str(tender),
                bid_filename="bid.docx",
                bid_path=str(bid),
                status="interpreting",
                config_snapshot="[]",
            )
        )
        await session.commit()

        tender_f, bid_f = await workspace.register_task_documents(
            session,
            task_id=task_id,
            tender_path=str(tender),
            tender_filename="tender.docx",
            bid_path=str(bid),
            bid_filename="bid.docx",
        )
        await session.commit()

        task = await session.get(DiagnosisTask, task_id)
        assert task.tender_file_id == tender_f.id
        assert task.bid_file_id == bid_f.id
        assert Path(task.tender_path).parent.name == "document"

        files = (await session.execute(select(WorkspaceFile).where(WorkspaceFile.task_id == task_id))).scalars().all()
        assert len(files) == 2
        jobs = (await session.execute(select(ParseJob).where(ParseJob.task_id == task_id))).scalars().all()
        assert len(jobs) == 2
        assert all(j.status == "queued" for j in jobs)
