import pytest
from datetime import datetime, timezone

from app import db
from app.models import DiagnosisTask, IndexJob
from app.services.bid_index_wait import (
    BidIndexBlockedError,
    wait_for_bid_index_ready,
)


@pytest.mark.asyncio
async def test_wait_returns_when_bid_index_ready(client, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-ready",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id="bid-1",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            IndexJob(
                task_id="t-idx-ready",
                file_id="bid-1",
                status="ready",
                stage="wiki",
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    await wait_for_bid_index_ready("t-idx-ready", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_fails_when_bid_index_failed(client, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-fail",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id="bid-2",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            IndexJob(
                task_id="t-idx-fail",
                file_id="bid-2",
                status="failed",
                stage="enrich",
                error_message="enrich boom",
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    with pytest.raises(BidIndexBlockedError, match="bid_index_failed"):
        await wait_for_bid_index_ready("t-idx-fail", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_fails_when_bid_file_id_missing(client, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-missing",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id=None,
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

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    with pytest.raises(BidIndexBlockedError, match="bid_file_missing"):
        await wait_for_bid_index_ready("t-idx-missing", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_times_out_when_still_queued(client, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-timeout",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id="bid-3",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            IndexJob(
                task_id="t-idx-timeout",
                file_id="bid-3",
                status="queued",
                stage="segments",
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    with pytest.raises(BidIndexBlockedError, match="bid_index_timeout"):
        await wait_for_bid_index_ready("t-idx-timeout", timeout=0.05)
