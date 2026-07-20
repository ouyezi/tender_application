from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, ExecutionNode
from app.services.execution_graph.tracker import ExecutionGraphTracker, get_tracker


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/eg.db", poolclass=NullPool
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="T-EG-001",
                tender_filename="t.pdf",
                tender_path="/tmp/t.pdf",
                bid_filename="b.docx",
                bid_path="/tmp/b.docx",
                status="interpreting",
            )
        )
        await session.commit()
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_graph_creates_static_nodes(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    async with db_session() as session:
        from sqlalchemy import func, select

        count = await session.scalar(
            select(func.count())
            .select_from(ExecutionNode)
            .where(ExecutionNode.task_id == "T-EG-001")
        )
    assert count >= 10
    assert "parse.tender" in {n.node_key for n in await _all_nodes(db_session, "T-EG-001")}


@pytest.mark.asyncio
async def test_track_marks_completed_with_duration(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    async with tracker.track("interpret", label="解读"):
        pass
    node = await _get_node(db_session, "T-EG-001", "interpret")
    assert node.status == "completed"
    assert node.started_at is not None
    assert node.ended_at is not None
    assert node.duration_ms is not None
    assert node.duration_ms >= 0


@pytest.mark.asyncio
async def test_track_failure_marks_failed(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    with pytest.raises(ValueError):
        async with tracker.track("checklist.generate"):
            raise ValueError("boom")
    node = await _get_node(db_session, "T-EG-001", "checklist.generate")
    assert node.status == "failed"
    import json

    assert "boom" in json.loads(node.meta).get("error", "")


@pytest.mark.asyncio
async def test_add_node_dynamic_batch(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    await tracker.add_node(
        node_key="diagnosis.category.c1",
        parent_key="diagnosis",
        label="资质审查",
        kind="batch",
    )
    async with tracker.track_node("diagnosis.category.c1"):
        pass
    node = await _get_node(db_session, "T-EG-001", "diagnosis.category.c1")
    assert node.status == "completed"
    assert node.parent_key == "diagnosis"


async def _all_nodes(session_factory, task_id: str):
    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(ExecutionNode).where(ExecutionNode.task_id == task_id)
        )
        return list(result.scalars().all())


async def _get_node(session_factory, task_id: str, key: str):
    nodes = await _all_nodes(session_factory, task_id)
    return next(n for n in nodes if n.node_key == key)
