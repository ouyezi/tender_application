from __future__ import annotations

import asyncio

from sqlalchemy import select

from app import config
from app import db as database
from app.models import DiagnosisTask, IndexJob
from app.services.agent_os import load_settings


class BidIndexBlockedError(RuntimeError):
    pass


async def wait_for_bid_index_ready(
    task_id: str,
    timeout: float | None = None,
) -> None:
    if timeout is not None:
        wait_timeout = float(timeout)
    else:
        wait_timeout = float(
            load_settings().batch_diagnosis_index_wait_timeout_seconds
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_timeout
    poll = float(config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS)

    while True:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise BidIndexBlockedError("task_missing")
            bid_file_id = task.bid_file_id
            if not bid_file_id:
                raise BidIndexBlockedError("bid_file_missing")

            result = await session.execute(
                select(IndexJob)
                .where(
                    IndexJob.task_id == task_id,
                    IndexJob.file_id == bid_file_id,
                )
                .order_by(IndexJob.id.desc())
            )
            job = result.scalars().first()
            if job is not None:
                if job.status == "ready":
                    return
                if job.status == "failed":
                    detail = job.error_message or ""
                    raise BidIndexBlockedError(
                        f"bid_index_failed:{detail}" if detail else "bid_index_failed"
                    )

        if loop.time() >= deadline:
            raise BidIndexBlockedError("bid_index_timeout")
        await asyncio.sleep(poll)
