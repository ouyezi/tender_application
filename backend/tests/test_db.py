import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import recover_interrupted_tasks
from app.models import Base, DiagnosisConfig, DiagnosisTask


@pytest.mark.asyncio
async def test_create_and_read_diagnosis_config(tmp_path):
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        row = DiagnosisConfig(
            title="企业资质核验",
            technique="对照招标资格要求",
            content_mode="description",
            content_text="所有资质文件",
            importance="high",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        config_id = row.id

    async with session_factory() as session:
        fetched = await session.get(DiagnosisConfig, config_id)
        assert fetched is not None
        assert fetched.title == "企业资质核验"
        assert fetched.importance == "high"

    await engine.dispose()


@pytest.mark.asyncio
async def test_recover_interrupted_tasks(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr("app.db.SessionLocal", session_factory)

    async with session_factory() as session:
        for task_id, status in [
            ("task-running", "running"),
            ("task-paused", "paused"),
            ("task-done", "completed"),
        ]:
            session.add(
                DiagnosisTask(
                    id=task_id,
                    tender_filename="tender.pdf",
                    tender_path="/uploads/tender.pdf",
                    bid_filename="bid.pdf",
                    bid_path="/uploads/bid.pdf",
                    status=status,
                )
            )
        await session.commit()

    await recover_interrupted_tasks()

    async with session_factory() as session:
        running = await session.get(DiagnosisTask, "task-running")
        paused = await session.get(DiagnosisTask, "task-paused")
        done = await session.get(DiagnosisTask, "task-done")
        assert running.status == "stopped"
        assert paused.status == "stopped"
        assert done.status == "completed"

    await engine.dispose()
