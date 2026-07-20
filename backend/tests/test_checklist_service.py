import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db
from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.models import (
    ChecklistCategory,
    ChecklistGeneration,
    ChecklistItem,
    DiagnosisTask,
    WorkspaceFile,
)
from app.services import artifact


def test_write_checklist_json_is_atomic_and_sanitizes_name(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(artifact, "UPLOAD_DIR", upload_dir)

    path = artifact.write_checklist_json(
        "task-1",
        "../检查 清单.json",
        {"中文": "内容", "value": 1},
    )

    assert path == upload_dir / "task-1" / "json" / "检查_清单.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "中文": "内容",
        "value": 1,
    }
    assert list(path.parent.iterdir()) == [path]


class StaticAgent:
    agent_type = "test"
    agent_version = "2026.1"

    def __init__(self, draft: ChecklistDraft):
        self.draft = draft
        self.context = None

    async def generate(self, *, task_id: str, context):
        del task_id
        self.context = context
        return self.draft


class FailingAgent:
    async def generate(self, *, task_id: str, context):
        del task_id, context
        raise RuntimeError("agent unavailable")


class InvalidResponseAgent(StaticAgent):
    def __init__(self, draft: ChecklistDraft):
        super().__init__(replace(draft, raw_response={"invalid": {"set-value"}}))


class BlockingAgent(StaticAgent):
    def __init__(self, draft: ChecklistDraft):
        super().__init__(draft)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, *, task_id: str, context):
        self.started.set()
        await self.release.wait()
        return await super().generate(task_id=task_id, context=context)


def valid_draft(tender_markdown: str) -> ChecklistDraft:
    source_text = "必须提交营业执照。"
    start = tender_markdown.index(source_text)
    category = ChecklistCategoryDraft(
        id="local-category",
        name="资格证明",
        description="核验主体资格",
        retrieval_query="营业执照",
        expected_locations=["资格审查"],
        sort_order=1,
    )
    item = ChecklistItemDraft(
        id="local-item",
        category_id=category.id,
        title="营业执照",
        requirement=source_text,
        technique="核验营业执照扫描件",
        importance="high",
        source_references=[
            {
                "coordinate_space": "segment",
                "segment_index": 0,
                "start": start,
                "end": start + len(source_text),
            }
        ],
        retrieval_hints=["营业执照"],
        expected_evidence=["有效营业执照"],
        compliance_rules={"satisfied": "材料完整"},
        consequence_rules={"bid_unusable": "资格审查不通过"},
        admin_config_refs=[7],
        sort_order=1,
    )
    return ChecklistDraft(
        schema_version="1",
        categories=[category],
        items=[item],
        raw_response={"provider": "fixture"},
    )


async def create_task_source(tmp_path: Path, task_id: str = "task-checklist"):
    tender_markdown = "# 资格要求\n必须提交营业执照。"
    tender_md_path = tmp_path / f"{task_id}-tender.md"
    interpret_md_path = tmp_path / f"{task_id}-interpret.md"
    tender_md_path.write_text(tender_markdown, encoding="utf-8")
    interpret_md_path.write_text("# 解读\n需核验资格材料。", encoding="utf-8")

    task = DiagnosisTask(
        id=task_id,
        tender_filename="tender.pdf",
        tender_path=str(tmp_path / "tender.pdf"),
        bid_filename="bid.pdf",
        bid_path=str(tmp_path / "bid.pdf"),
        tender_file_id=f"file-{task_id}",
        status="generating_checklist",
        config_snapshot=json.dumps([{"id": 7, "title": "资格审查"}]),
        interpret_md_path=str(interpret_md_path),
    )
    workspace_file = WorkspaceFile(
        id=f"file-{task_id}",
        task_id=task_id,
        label="招标文件",
        original_filename="tender.pdf",
        stored_path=str(tmp_path / "tender.pdf"),
        kind="document",
        ext=".pdf",
        parse_status="succeeded",
        md_path=str(tender_md_path),
    )
    async with db.SessionLocal() as session:
        session.add_all([task, workspace_file])
        await session.commit()
    return tender_markdown


@pytest.mark.asyncio
async def test_generate_persists_offline_diagnosis_mode_in_get_report(
    client, tmp_path
):
    del client
    from app.services.checklist_service import ChecklistService, get_report

    tender_markdown = await create_task_source(tmp_path)
    draft = valid_draft(tender_markdown)
    draft = replace(
        draft,
        items=[replace(draft.items[0], diagnosis_mode="offline")],
    )

    await ChecklistService(StaticAgent(draft)).generate_for_task("task-checklist")
    report = await get_report("task-checklist")

    assert report["categories"][0]["items"][0]["diagnosis_mode"] == "offline"


@pytest.mark.asyncio
async def test_invalid_diagnosis_mode_normalizes_to_file_without_failing(
    client, tmp_path
):
    del client
    from app.services.checklist_service import ChecklistService, get_report

    tender_markdown = await create_task_source(tmp_path)
    draft = valid_draft(tender_markdown)
    draft = replace(
        draft,
        items=[replace(draft.items[0], diagnosis_mode="weird")],
    )

    await ChecklistService(StaticAgent(draft)).generate_for_task("task-checklist")
    report = await get_report("task-checklist")

    assert report["categories"][0]["items"][0]["diagnosis_mode"] == "file"


@pytest.mark.asyncio
async def test_generate_persists_rows_pointer_progress_and_artifacts(
    client, tmp_path
):
    del client
    from app.services.checklist_service import ChecklistService

    tender_markdown = await create_task_source(tmp_path)
    draft = replace(
        valid_draft(tender_markdown),
        raw_response={
            "provider": "fixture-provider",
            "token": "test-secret",
        },
    )
    agent = StaticAgent(draft)

    generation_id = await ChecklistService(agent).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = await session.get(ChecklistGeneration, generation_id)
        task = await session.get(DiagnosisTask, "task-checklist")
        category = (
            await session.scalars(
                select(ChecklistCategory).where(
                    ChecklistCategory.generation_id == generation_id
                )
            )
        ).one()
        item = (
            await session.scalars(
                select(ChecklistItem).where(
                    ChecklistItem.generation_id == generation_id
                )
            )
        ).one()

    assert generation.status == "succeeded"
    assert generation.agent_type == "test"
    assert generation.agent_version == "2026.1"
    assert generation.input_hash and len(generation.input_hash) == 64
    assert generation.finished_at is not None
    assert task.current_checklist_generation_id == generation_id
    assert (task.progress_done, task.progress_total) == (0, 1)
    assert category.id.startswith(f"cat-{generation_id}-")
    assert item.id.startswith(f"item-{generation_id}-")
    assert item.category_id == category.id
    assert json.loads(item.source_references)[0]["segment_index"] == 0
    assert json.loads(category.expected_locations) == ["资格审查"]

    raw_path = Path(generation.raw_response_path)
    formal_path = (
        artifact.artifact_root("task-checklist")
        / "json"
        / f"checklist-generation-{generation_id}.json"
    )
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    formal_payload = json.loads(formal_path.read_text(encoding="utf-8"))
    assert raw_payload["raw_response"]["provider"] == "fixture-provider"
    assert formal_payload["items"][0]["id"] == item.id
    assert set(formal_payload) == {"schema_version", "categories", "items"}
    assert "raw_response" not in formal_payload
    assert "fixture-provider" not in formal_path.read_text(encoding="utf-8")
    assert "test-secret" not in formal_path.read_text(encoding="utf-8")
    assert not raw_path.is_relative_to(artifact.UPLOAD_DIR)
    assert agent.context.segments == [tender_markdown]
    assert not list(raw_path.parent.glob("*.tmp"))


@pytest.mark.asyncio
async def test_global_ids_do_not_conflict_between_generations(client, tmp_path):
    del client
    from app.services.checklist_service import ChecklistService

    tender_markdown = await create_task_source(tmp_path)
    service = ChecklistService(StaticAgent(valid_draft(tender_markdown)))

    first_id = await service.generate_for_task("task-checklist")
    second_markdown = await create_task_source(tmp_path, "task-checklist-2")
    second_id = await ChecklistService(
        StaticAgent(valid_draft(second_markdown))
    ).generate_for_task("task-checklist-2")

    async with db.SessionLocal() as session:
        categories = (
            await session.scalars(
                select(ChecklistCategory).order_by(ChecklistCategory.generation_id)
            )
        ).all()
        items = (
            await session.scalars(
                select(ChecklistItem).order_by(ChecklistItem.generation_id)
            )
        ).all()
        first_task = await session.get(DiagnosisTask, "task-checklist")
        second_task = await session.get(DiagnosisTask, "task-checklist-2")

    assert first_id != second_id
    assert len({category.id for category in categories}) == 2
    assert len({item.id for item in items}) == 2
    assert [item.category_id for item in items] == [
        category.id for category in categories
    ]
    assert first_task.current_checklist_generation_id == first_id
    assert second_task.current_checklist_generation_id == second_id


@pytest.mark.asyncio
async def test_staged_payload_is_private_until_database_publish(
    client, tmp_path
):
    del client
    from app.services.checklist_service import ChecklistService

    class PausingPublishService(ChecklistService):
        def __init__(self, agent):
            super().__init__(agent)
            self.publish_started = asyncio.Event()
            self.continue_publish = asyncio.Event()

        async def _publish(self, *args, **kwargs):
            self.publish_started.set()
            await self.continue_publish.wait()
            await super()._publish(*args, **kwargs)

    tender_markdown = await create_task_source(tmp_path)
    service = PausingPublishService(
        StaticAgent(valid_draft(tender_markdown))
    )
    call = asyncio.create_task(service.generate_for_task("task-checklist"))
    await service.publish_started.wait()

    public_json_dir = artifact.artifact_root("task-checklist") / "json"
    private_staging_dir = (
        artifact.REPORT_DIR / "task-checklist" / "staging"
    )
    assert not list(public_json_dir.glob("checklist-generation-*.json"))
    assert not list(public_json_dir.glob("*staged*"))
    assert list(private_staging_dir.glob("*staged*"))

    service.continue_publish.set()
    generation_id = await call
    assert (
        public_json_dir / f"checklist-generation-{generation_id}.json"
    ).is_file()
    assert not list(private_staging_dir.glob("*staged*"))


@pytest.mark.asyncio
async def test_concurrent_generation_publishes_once_without_failing_task(
    client, tmp_path
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
        _TASK_LOCKS,
    )

    tender_markdown = await create_task_source(tmp_path)
    blocking_agent = BlockingAgent(valid_draft(tender_markdown))
    first_call = asyncio.create_task(
        ChecklistService(blocking_agent).generate_for_task("task-checklist")
    )
    await blocking_agent.started.wait()
    second_call = asyncio.create_task(
        ChecklistService(
            StaticAgent(valid_draft(tender_markdown))
        ).generate_for_task("task-checklist")
    )
    await asyncio.sleep(0)
    blocking_agent.release.set()

    first_result, second_result = await asyncio.gather(
        first_call,
        second_call,
        return_exceptions=True,
    )

    assert isinstance(first_result, int)
    assert isinstance(second_result, ChecklistValidationError)
    assert str(second_result) == "checklist already published"
    async with db.SessionLocal() as session:
        generations = (await session.scalars(select(ChecklistGeneration))).all()
        task = await session.get(DiagnosisTask, "task-checklist")

    assert len(generations) == 1
    assert generations[0].status == "succeeded"
    assert task.current_checklist_generation_id == first_result
    assert task.status != "failed"
    assert _TASK_LOCKS == {}


@pytest.mark.asyncio
async def test_cancellation_marks_attempt_and_task_failed(client, tmp_path):
    del client
    from app.services.checklist_service import ChecklistService, _TASK_LOCKS

    tender_markdown = await create_task_source(tmp_path)
    agent = BlockingAgent(valid_draft(tender_markdown))
    call = asyncio.create_task(
        ChecklistService(agent).generate_for_task("task-checklist")
    )
    await agent.started.wait()

    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")

    assert generation.status == "failed"
    assert generation.error_message == "checklist_generation_cancelled"
    assert generation.finished_at is not None
    assert task.status == "failed"
    assert task.error_message == "checklist_generation_cancelled"
    assert task.current_checklist_generation_id is None
    assert _TASK_LOCKS == {}


@pytest.mark.asyncio
async def test_repeated_cancellation_waits_for_cleanup_before_retry(
    client, tmp_path
):
    del client
    from app.services.checklist_service import ChecklistService, _TASK_LOCKS

    class SlowCleanupService(ChecklistService):
        def __init__(self, agent):
            super().__init__(agent)
            self.cleanup_started = asyncio.Event()
            self.finish_cleanup = asyncio.Event()

        async def _fail(self, *args, **kwargs):
            self.cleanup_started.set()
            await self.finish_cleanup.wait()
            await super()._fail(*args, **kwargs)

    tender_markdown = await create_task_source(tmp_path)
    blocking_agent = BlockingAgent(valid_draft(tender_markdown))
    service = SlowCleanupService(blocking_agent)
    cancelled_call = asyncio.create_task(
        service.generate_for_task("task-checklist")
    )
    await blocking_agent.started.wait()

    cancelled_call.cancel()
    await service.cleanup_started.wait()
    for _ in range(3):
        cancelled_call.cancel()
        await asyncio.sleep(0)
    assert not cancelled_call.done()
    retry_call = asyncio.create_task(
        ChecklistService(
            StaticAgent(valid_draft(tender_markdown))
        ).generate_for_task("task-checklist")
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    retry_finished_before_cleanup = retry_call.done()
    service.finish_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await cancelled_call
    generation_id = await retry_call

    async with db.SessionLocal() as session:
        generations = (
            await session.scalars(
                select(ChecklistGeneration).order_by(ChecklistGeneration.id)
            )
        ).all()
        task = await session.get(DiagnosisTask, "task-checklist")

    assert retry_finished_before_cleanup is False
    assert [generation.status for generation in generations] == [
        "failed",
        "succeeded",
    ]
    assert task.current_checklist_generation_id == generation_id
    assert task.error_message is None
    assert _TASK_LOCKS == {}


@pytest.mark.asyncio
async def test_unserializable_raw_response_saves_fallback_then_fails_validation(
    client, tmp_path
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
    )

    tender_markdown = await create_task_source(tmp_path)

    with pytest.raises(
        ChecklistValidationError,
        match="raw_response must be JSON serializable",
    ):
        await ChecklistService(
            InvalidResponseAgent(valid_draft(tender_markdown))
        ).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    raw_path = Path(generation.raw_response_path)
    fallback = json.loads(raw_path.read_text(encoding="utf-8"))
    assert fallback["serialization_error"]
    assert "set-value" in fallback["safe_repr"]
    assert generation.status == "failed"
    assert generation.error_message == "checklist_validation_failed"
    assert task.status == "failed"
    assert task.error_message == "checklist_validation_failed"
    assert task.current_checklist_generation_id is None
    assert categories == []
    assert items == []


@pytest.mark.asyncio
async def test_mixed_raw_response_keys_save_fallback_then_fail_validation(
    client, tmp_path
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
    )

    tender_markdown = await create_task_source(tmp_path)
    draft = replace(
        valid_draft(tender_markdown),
        raw_response={"a": 1, 2: "b"},
    )

    with pytest.raises(
        ChecklistValidationError,
        match="raw_response must be JSON serializable",
    ):
        await ChecklistService(StaticAgent(draft)).generate_for_task(
            "task-checklist"
        )

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    raw_path = Path(generation.raw_response_path)
    fallback = json.loads(raw_path.read_text(encoding="utf-8"))
    assert "serialization_error" in fallback
    assert "'a': 1" in fallback["safe_repr"]
    assert "2: 'b'" in fallback["safe_repr"]
    assert generation.status == "failed"
    assert task.status == "failed"
    assert task.current_checklist_generation_id is None
    assert categories == []
    assert items == []


@pytest.mark.asyncio
async def test_surrogate_raw_response_saves_utf8_fallback_then_fails_validation(
    client, tmp_path
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
    )

    tender_markdown = await create_task_source(tmp_path)
    draft = replace(
        valid_draft(tender_markdown),
        raw_response={"invalid_unicode": "\ud800"},
    )

    with pytest.raises(
        ChecklistValidationError,
        match="raw_response must be JSON serializable",
    ):
        await ChecklistService(StaticAgent(draft)).generate_for_task(
            "task-checklist"
        )

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    raw_path = Path(generation.raw_response_path)
    fallback = json.loads(raw_path.read_text(encoding="utf-8"))
    assert "UnicodeEncodeError" in fallback["serialization_error"]
    assert "\\ud800" in fallback["safe_repr"]
    assert generation.status == "failed"
    assert task.status == "failed"
    assert task.current_checklist_generation_id is None
    assert categories == []
    assert items == []


def invalid_json_value(case_name: str):
    if case_name == "nan":
        return {"value": float("nan")}
    circular: dict[str, Any] = {}
    circular["self"] = circular
    return circular


@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", ["nan", "circular"])
async def test_invalid_json_values_use_fallback_and_validation_error(
    client, tmp_path, case_name
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
    )

    tender_markdown = await create_task_source(tmp_path)
    draft = replace(
        valid_draft(tender_markdown),
        raw_response=invalid_json_value(case_name),
    )

    with pytest.raises(
        ChecklistValidationError,
        match="raw_response must be JSON serializable",
    ):
        await ChecklistService(StaticAgent(draft)).generate_for_task(
            "task-checklist"
        )

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")

    fallback = json.loads(
        Path(generation.raw_response_path).read_text(encoding="utf-8")
    )
    assert fallback["serialization_error"]
    assert generation.status == "failed"
    assert task.current_checklist_generation_id is None


def invalid_draft_cases(tender_markdown: str):
    draft = valid_draft(tender_markdown)
    item = draft.items[0]
    too_many = [
        replace(
            item,
            id=f"item-{index}",
            title=f"营业执照 {index}",
            requirement=f"要求 {index}",
        )
        for index in range(21)
    ]
    return [
        (replace(draft, items=[replace(item, source_references=[])]), "source_references"),
        (replace(draft, items=[replace(item, importance="critical")]), "importance"),
        (replace(draft, items=[replace(item, category_id="missing")]), "category"),
        (
            replace(draft, items=[item, replace(item, id="other-item")]),
            "duplicate item",
        ),
        (
            replace(
                draft,
                items=[replace(item, compliance_rules={"unknown": "bad"})],
            ),
            "compliance_rules",
        ),
        (replace(draft, items=too_many), "maximum"),
        (
            replace(
                draft,
                items=[
                    replace(
                        item,
                        source_references=[
                            {
                                "coordinate_space": "segment",
                                "segment_index": 0,
                                "start": 0,
                                "end": 999,
                            }
                        ],
                    )
                ],
            ),
            "offset",
        ),
        (
            replace(draft, items=[replace(item, admin_config_refs=[999])]),
            "admin_config_refs",
        ),
    ]


def invalid_field_type_cases(tender_markdown: str):
    draft = valid_draft(tender_markdown)
    category = draft.categories[0]
    item = draft.items[0]
    source_reference = item.source_references[0]
    return [
        (
            replace(draft, categories=[replace(category, sort_order="1")]),
            "category sort_order",
        ),
        (
            replace(draft, items=[replace(item, sort_order="1")]),
            "item sort_order",
        ),
        (
            replace(
                draft,
                categories=[replace(category, expected_locations=["资格审查", 1])],
            ),
            "category expected_locations",
        ),
        (
            replace(draft, items=[replace(item, retrieval_hints=["营业执照", False])]),
            "item retrieval_hints",
        ),
        (
            replace(draft, items=[replace(item, admin_config_refs=[True])]),
            "item admin_config_refs",
        ),
        (
            replace(
                draft,
                items=[
                    replace(
                        item,
                        source_references=[{**source_reference, "start": True}],
                    )
                ],
            ),
            "source start",
        ),
        (
            replace(
                draft,
                items=[
                    replace(
                        item,
                        compliance_rules={"satisfied": 1},
                    )
                ],
            ),
            "item compliance_rules",
        ),
        (
            replace(
                draft,
                items=[
                    replace(
                        item,
                        source_references=[
                            {**source_reference, "synthetic": "false"}
                        ],
                    )
                ],
            ),
            "source synthetic",
        ),
        (replace(draft, raw_response=[]), "raw_response"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", range(9))
async def test_invalid_field_types_raise_validation_errors(
    client, tmp_path, case_index
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
    )

    tender_markdown = await create_task_source(tmp_path)
    draft, expected_field = invalid_field_type_cases(tender_markdown)[case_index]

    with pytest.raises(ChecklistValidationError, match=expected_field):
        await ChecklistService(StaticAgent(draft)).generate_for_task("task-checklist")


@pytest.mark.asyncio
@pytest.mark.parametrize("case_index", range(8))
async def test_invalid_drafts_fail_cleanly(client, tmp_path, case_index):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        ChecklistValidationError,
    )

    tender_markdown = await create_task_source(tmp_path)
    draft, expected_error = invalid_draft_cases(tender_markdown)[case_index]

    with pytest.raises(ChecklistValidationError, match=expected_error):
        await ChecklistService(StaticAgent(draft)).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    assert generation.status == "failed"
    assert generation.error_message == "checklist_validation_failed"
    assert Path(generation.raw_response_path).is_file()
    assert generation.finished_at is not None
    assert task.status == "failed"
    assert task.error_message == "checklist_validation_failed"
    assert task.current_checklist_generation_id is None
    assert categories == []
    assert items == []


@pytest.mark.asyncio
async def test_agent_exception_marks_generation_and_task_failed(client, tmp_path):
    del client
    from app.services.checklist_service import ChecklistService

    await create_task_source(tmp_path)

    with pytest.raises(RuntimeError, match="agent unavailable"):
        await ChecklistService(FailingAgent()).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")

    assert generation.status == "failed"
    assert generation.raw_response_path is None
    assert task.status == "failed"
    assert task.current_checklist_generation_id is None


@pytest.mark.asyncio
async def test_unexpected_exception_does_not_leak_sensitive_details(
    client, tmp_path
):
    del client
    from app.services.checklist_service import ChecklistService

    class SensitiveFailingAgent:
        async def generate(self, *, task_id: str, context):
            del task_id, context
            raise RuntimeError(
                "secret-token at /Users/private/credentials.json"
            )

    await create_task_source(tmp_path)

    with pytest.raises(RuntimeError):
        await ChecklistService(SensitiveFailingAgent()).generate_for_task(
            "task-checklist"
        )

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")

    assert generation.error_message == "checklist_generation_failed"
    assert task.error_message == "checklist_generation_failed"
    assert "secret-token" not in task.error_message
    assert "/Users/" not in task.error_message


@pytest.mark.asyncio
async def test_publish_database_error_rolls_back_all_formal_rows(
    client, tmp_path, monkeypatch
):
    del client
    from app.services.checklist_service import ChecklistService

    tender_markdown = await create_task_source(tmp_path)
    original_flush = AsyncSession.flush

    async def fail_when_items_are_pending(self, objects=None):
        if any(isinstance(value, ChecklistItem) for value in self.new):
            raise RuntimeError("forced publish failure")
        return await original_flush(self, objects)

    monkeypatch.setattr(AsyncSession, "flush", fail_when_items_are_pending)

    with pytest.raises(RuntimeError, match="forced publish failure"):
        await ChecklistService(
            StaticAgent(valid_draft(tender_markdown))
        ).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    assert generation.status == "failed"
    assert task.current_checklist_generation_id is None
    assert categories == []
    assert items == []
    json_dir = artifact.artifact_root("task-checklist") / "json"
    assert not list(json_dir.glob("checklist-generation-*.json"))
    assert not list(json_dir.glob("*staged*"))
    private_staging_dir = (
        artifact.REPORT_DIR / "task-checklist" / "staging"
    )
    assert not list(private_staging_dir.glob("*staged*"))


@pytest.mark.asyncio
async def test_artifact_promotion_failure_compensates_published_database(
    client, tmp_path, monkeypatch
):
    del client
    from app.services.checklist_service import ChecklistService

    tender_markdown = await create_task_source(tmp_path)

    def fail_promotion(*args, **kwargs):
        del args, kwargs
        raise OSError("secret-token /Users/private/artifact.json")

    monkeypatch.setattr(
        "app.services.checklist_service.promote_staged_checklist_json",
        fail_promotion,
    )

    with pytest.raises(RuntimeError, match="checklist_artifact_publish_failed"):
        await ChecklistService(
            StaticAgent(valid_draft(tender_markdown))
        ).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    assert generation.status == "failed"
    assert generation.error_message == "checklist_generation_failed"
    assert task.status == "failed"
    assert task.error_message == "checklist_generation_failed"
    assert task.current_checklist_generation_id is None
    assert categories == []
    assert items == []
    json_dir = artifact.artifact_root("task-checklist") / "json"
    assert not list(json_dir.glob("checklist-generation-*.json"))
    assert not list(json_dir.glob("*staged*"))
    private_staging_dir = (
        artifact.REPORT_DIR / "task-checklist" / "staging"
    )
    assert not list(private_staging_dir.glob("*staged*"))


@pytest.mark.asyncio
async def test_process_interrupt_preserves_staged_for_publication_recovery(
    client, tmp_path, monkeypatch
):
    del client
    from app.services.checklist_service import ChecklistService

    tender_markdown = await create_task_source(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(
            "app.services.checklist_service.promote_staged_checklist_json",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                KeyboardInterrupt("simulated process interrupt")
            ),
        )
        with pytest.raises(KeyboardInterrupt, match="simulated process interrupt"):
            await ChecklistService(
                StaticAgent(valid_draft(tender_markdown))
            ).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")
        categories = (await session.scalars(select(ChecklistCategory))).all()
        items = (await session.scalars(select(ChecklistItem))).all()

    public_path = (
        artifact.artifact_root("task-checklist")
        / "json"
        / f"checklist-generation-{generation.id}.json"
    )
    private_staging_dir = (
        artifact.REPORT_DIR / "task-checklist" / "staging"
    )
    staged_paths = list(private_staging_dir.glob("*staged*"))
    assert generation.status == "succeeded"
    assert task.current_checklist_generation_id == generation.id
    assert categories
    assert items
    assert not public_path.exists()
    assert len(staged_paths) == 1

    from app.services.checklist_service import recover_checklist_publications

    await recover_checklist_publications()

    assert public_path.is_file()
    assert not list(private_staging_dir.glob("*staged*"))
    recovered_payload = json.loads(public_path.read_text(encoding="utf-8"))
    assert recovered_payload["schema_version"] == "1"
    assert recovered_payload["items"]


@pytest.mark.asyncio
async def test_interrupt_after_publish_commit_preserves_staged_for_recovery(
    client, tmp_path, monkeypatch
):
    del client
    from app.services.checklist_service import ChecklistService

    tender_markdown = await create_task_source(tmp_path)
    original_publish = ChecklistService._publish

    async def publish_then_interrupt(self, *args, **kwargs):
        await original_publish(self, *args, **kwargs)
        raise KeyboardInterrupt("interrupt after database commit")

    with monkeypatch.context() as patch:
        patch.setattr(ChecklistService, "_publish", publish_then_interrupt)
        with pytest.raises(KeyboardInterrupt, match="after database commit"):
            await ChecklistService(
                StaticAgent(valid_draft(tender_markdown))
            ).generate_for_task("task-checklist")

    async with db.SessionLocal() as session:
        generation = (await session.scalars(select(ChecklistGeneration))).one()
        task = await session.get(DiagnosisTask, "task-checklist")

    filename = f"checklist-generation-{generation.id}.json"
    staged_path = artifact.staged_checklist_path(
        "task-checklist",
        filename,
    )
    final_path = artifact.checklist_json_path(
        "task-checklist",
        filename,
    )
    assert generation.status == "succeeded"
    assert task.current_checklist_generation_id == generation.id
    assert staged_path.is_file()
    assert not final_path.exists()

    from app.services.checklist_service import recover_checklist_publications

    await recover_checklist_publications()

    assert final_path.is_file()
    assert not staged_path.exists()
    assert json.loads(final_path.read_text(encoding="utf-8"))["items"]


@pytest.mark.asyncio
async def test_recovery_ignores_failed_and_noncurrent_staged_files(
    client, tmp_path
):
    del client
    from app.services.checklist_service import recover_checklist_publications

    await create_task_source(tmp_path, "failed-task")
    await create_task_source(tmp_path, "noncurrent-task")
    async with db.SessionLocal() as session:
        failed = ChecklistGeneration(
            task_id="failed-task",
            status="failed",
            input_hash="failed",
        )
        noncurrent = ChecklistGeneration(
            task_id="noncurrent-task",
            status="succeeded",
            input_hash="noncurrent",
        )
        session.add_all([failed, noncurrent])
        await session.flush()
        failed_task = await session.get(DiagnosisTask, "failed-task")
        failed_task.current_checklist_generation_id = failed.id
        await session.commit()

    payload = {"schema_version": "1", "categories": [], "items": []}
    failed_staged = artifact.stage_checklist_json(
        "failed-task",
        f"checklist-generation-{failed.id}.json",
        payload,
    )
    noncurrent_staged = artifact.stage_checklist_json(
        "noncurrent-task",
        f"checklist-generation-{noncurrent.id}.json",
        payload,
    )

    await recover_checklist_publications()

    assert not failed_staged.exists()
    assert not noncurrent_staged.exists()
    assert not artifact.checklist_json_path(
        "failed-task",
        f"checklist-generation-{failed.id}.json",
    ).exists()
    assert not artifact.checklist_json_path(
        "noncurrent-task",
        f"checklist-generation-{noncurrent.id}.json",
    ).exists()


@pytest.mark.asyncio
async def test_recovery_removes_staged_when_formal_artifact_exists(
    client, tmp_path
):
    del client
    from app.services.checklist_service import (
        ChecklistService,
        recover_checklist_publications,
    )

    tender_markdown = await create_task_source(tmp_path)
    generation_id = await ChecklistService(
        StaticAgent(valid_draft(tender_markdown))
    ).generate_for_task("task-checklist")
    filename = f"checklist-generation-{generation_id}.json"
    staged_path = artifact.stage_checklist_json(
        "task-checklist",
        filename,
        {"schema_version": "stale", "categories": [], "items": []},
    )

    await recover_checklist_publications()

    assert not staged_path.exists()
    formal_payload = json.loads(
        artifact.checklist_json_path(
            "task-checklist",
            filename,
        ).read_text(encoding="utf-8")
    )
    assert formal_payload["schema_version"] == "1"


def test_compliance_rules_allows_empty_cannot_satisfy():
    from app.services.checklist_service import (
        _COMPLIANCE_KEYS,
        _COMPLIANCE_OPTIONAL_EMPTY_KEYS,
        _require_rules,
    )

    _require_rules(
        {
            "satisfied": "满足",
            "violated": "违反",
            "cannot_satisfy": "",
            "insufficient_evidence": "证据不足",
        },
        "item compliance_rules",
        _COMPLIANCE_KEYS,
        optional_empty_keys=_COMPLIANCE_OPTIONAL_EMPTY_KEYS,
    )


def test_compliance_rules_allows_empty_insufficient_evidence():
    from app.services.checklist_service import (
        _COMPLIANCE_KEYS,
        _COMPLIANCE_OPTIONAL_EMPTY_KEYS,
        _require_rules,
    )

    _require_rules(
        {
            "satisfied": "满足",
            "violated": "违反",
            "cannot_satisfy": "",
            "insufficient_evidence": "",
        },
        "item compliance_rules",
        _COMPLIANCE_KEYS,
        optional_empty_keys=_COMPLIANCE_OPTIONAL_EMPTY_KEYS,
    )


def test_compliance_rules_rejects_empty_satisfied():
    from app.services.checklist_service import (
        ChecklistValidationError,
        _COMPLIANCE_KEYS,
        _COMPLIANCE_OPTIONAL_EMPTY_KEYS,
        _require_rules,
    )

    with pytest.raises(ChecklistValidationError, match="non-empty"):
        _require_rules(
            {
                "satisfied": "",
                "violated": "违反",
                "cannot_satisfy": "",
                "insufficient_evidence": "",
            },
            "item compliance_rules",
            _COMPLIANCE_KEYS,
            optional_empty_keys=_COMPLIANCE_OPTIONAL_EMPTY_KEYS,
        )
