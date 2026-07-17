import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.db import recover_interrupted_tasks
from app.models import Base, DiagnosisConfig, DiagnosisResult, DiagnosisTask


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
async def test_persist_checklist_generation_and_diagnosis_result(tmp_path):
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        task = DiagnosisTask(
            id="task-checklist",
            tender_filename="tender.pdf",
            tender_path="/uploads/tender.pdf",
            bid_filename="bid.pdf",
            bid_path="/uploads/bid.pdf",
        )
        session.add(task)
        await session.commit()

        generation = models.ChecklistGeneration(
            task_id=task.id,
            status="completed",
            input_hash="input-hash",
        )
        session.add(generation)
        await session.flush()

        category = models.ChecklistCategory(
            id="category-qualification",
            generation_id=generation.id,
            name="资格要求",
            description="检查投标人资格",
            retrieval_query="资格证书",
        )
        item = models.ChecklistItem(
            id="item-business-license",
            generation_id=generation.id,
            category_id=category.id,
            title="营业执照",
            requirement="营业执照必须有效",
            technique="核对有效期",
            importance="high",
            source_references="[]",
            retrieval_hints="[]",
            expected_evidence="[]",
            compliance_rules="[]",
            consequence_rules="[]",
        )
        session.add_all([category, item])
        await session.flush()

        task.current_checklist_generation_id = generation.id
        result = DiagnosisResult(
            task_id=task.id,
            checklist_item_id=item.id,
            content_title="营业执照",
            result="通过",
            compliance_status="satisfied",
            consequence_tags='["general_risk"]',
        )
        session.add(result)
        await session.commit()
        result_id = result.id
        generation_id = generation.id

    async with session_factory() as session:
        fetched_task = await session.get(DiagnosisTask, "task-checklist")
        fetched_result = await session.get(DiagnosisResult, result_id)
        assert fetched_task is not None
        assert fetched_result is not None
        assert fetched_task.current_checklist_generation_id == generation_id
        assert fetched_result.checklist_item_id == "item-business-license"
        assert fetched_result.compliance_status == "satisfied"
        assert fetched_result.consequence_tags == '["general_risk"]'

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
            ("task-interpreting", "interpreting"),
            ("task-diagnosing", "diagnosing"),
            ("task-running", "running"),
            ("task-paused", "paused"),
            ("task-generating-checklist", "generating_checklist"),
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
        interpreting = await session.get(DiagnosisTask, "task-interpreting")
        diagnosing = await session.get(DiagnosisTask, "task-diagnosing")
        running = await session.get(DiagnosisTask, "task-running")
        paused = await session.get(DiagnosisTask, "task-paused")
        generating_checklist = await session.get(
            DiagnosisTask, "task-generating-checklist"
        )
        done = await session.get(DiagnosisTask, "task-done")
        assert interpreting.status == "stopped"
        assert diagnosing.status == "stopped"
        assert running.status == "stopped"
        assert paused.status == "stopped"
        assert generating_checklist.status == "stopped"
        assert done.status == "completed"

    await engine.dispose()
