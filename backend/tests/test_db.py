import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models
from app.db import recover_interrupted_tasks
from app.models import (
    Base,
    ChecklistGeneration,
    DiagnosisConfig,
    DiagnosisResult,
    DiagnosisTask,
)


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
async def test_persist_task_checklist_and_structured_result(tmp_path):
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
            source_references='["招标文件第 3 页"]',
            retrieval_hints='["营业执照"]',
            expected_evidence='["有效营业执照"]',
            compliance_rules='["证照在有效期内"]',
            consequence_rules='["无效时标记重大风险"]',
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
        fetched_generation = await session.get(
            models.ChecklistGeneration, generation_id
        )
        fetched_category = await session.get(
            models.ChecklistCategory, "category-qualification"
        )
        fetched_item = await session.get(
            models.ChecklistItem, "item-business-license"
        )
        assert fetched_task is not None
        assert fetched_result is not None
        assert fetched_generation is not None
        assert fetched_category is not None
        assert fetched_item is not None

        assert fetched_task.current_checklist_generation_id == generation_id

        assert fetched_generation.task_id == "task-checklist"
        assert fetched_generation.status == "completed"
        assert fetched_generation.agent_type == "mock"
        assert fetched_generation.agent_version == "1"
        assert fetched_generation.schema_version == "1"
        assert fetched_generation.input_hash == "input-hash"
        assert fetched_generation.admin_config_snapshot == "[]"
        assert fetched_generation.raw_response_path is None
        assert fetched_generation.error_message is None
        assert fetched_generation.created_at is not None
        assert fetched_generation.finished_at is None

        assert fetched_category.generation_id == generation_id
        assert fetched_category.name == "资格要求"
        assert fetched_category.description == "检查投标人资格"
        assert fetched_category.retrieval_query == "资格证书"
        assert fetched_category.expected_locations == "[]"
        assert fetched_category.sort_order == 0

        assert fetched_item.generation_id == generation_id
        assert fetched_item.category_id == "category-qualification"
        assert fetched_item.title == "营业执照"
        assert fetched_item.requirement == "营业执照必须有效"
        assert fetched_item.technique == "核对有效期"
        assert fetched_item.importance == "high"
        assert fetched_item.source_references == '["招标文件第 3 页"]'
        assert fetched_item.retrieval_hints == '["营业执照"]'
        assert fetched_item.expected_evidence == '["有效营业执照"]'
        assert fetched_item.compliance_rules == '["证照在有效期内"]'
        assert fetched_item.consequence_rules == '["无效时标记重大风险"]'
        assert fetched_item.admin_config_refs == "[]"
        assert fetched_item.content_source == "precise_search"
        assert fetched_item.content_target == "{}"
        assert fetched_item.sort_order == 0
        assert fetched_item.created_at is not None

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
        session.add_all(
            [
                ChecklistGeneration(
                    task_id="task-generating-checklist",
                    status="generating",
                    input_hash="interrupted-hash",
                ),
                ChecklistGeneration(
                    task_id="task-done",
                    status="succeeded",
                    input_hash="completed-hash",
                ),
            ]
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
        interrupted_generation = (
            await session.scalars(
                select(ChecklistGeneration).where(
                    ChecklistGeneration.input_hash == "interrupted-hash"
                )
            )
        ).one()
        completed_generation = (
            await session.scalars(
                select(ChecklistGeneration).where(
                    ChecklistGeneration.input_hash == "completed-hash"
                )
            )
        ).one()
        assert interpreting.status == "stopped"
        assert diagnosing.status == "stopped"
        assert running.status == "stopped"
        assert paused.status == "stopped"
        assert generating_checklist.status == "stopped"
        assert done.status == "completed"
        assert interrupted_generation.status == "failed"
        assert interrupted_generation.error_message == "interrupted"
        assert interrupted_generation.finished_at is not None
        assert completed_generation.status == "succeeded"

    await engine.dispose()
