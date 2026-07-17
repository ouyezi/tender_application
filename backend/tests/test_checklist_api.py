import asyncio
import json

import pytest
from sqlalchemy import select

from app import db
from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.models import ChecklistItem, DiagnosisTask, WorkspaceFile
from app.schemas import ResultOut
from app.services.checklist_service import ChecklistService


class StaticAgent:
    agent_type = "test"
    agent_version = "2026.1"

    def __init__(self, draft):
        self.draft = draft

    async def generate(self, *, task_id, context):
        del task_id, context
        return self.draft


def _draft(tender_markdown):
    requirement = "必须提交营业执照。"
    start = tender_markdown.index(requirement)
    return ChecklistDraft(
        schema_version="1",
        categories=[
            ChecklistCategoryDraft(
                id="category",
                name="资格证明",
                description="核验主体资格",
                retrieval_query="营业执照",
                expected_locations=["资格审查"],
                sort_order=1,
            )
        ],
        items=[
            ChecklistItemDraft(
                id="item",
                category_id="category",
                title="营业执照",
                requirement=requirement,
                technique="核验扫描件",
                importance="high",
                source_references=[
                    {
                        "coordinate_space": "segment",
                        "segment_index": 0,
                        "start": start,
                        "end": start + len(requirement),
                    }
                ],
                retrieval_hints=["营业执照"],
                expected_evidence=["有效营业执照"],
                compliance_rules={"satisfied": "材料完整"},
                consequence_rules={"bid_unusable": "资格审查不通过"},
                admin_config_refs=[7],
                sort_order=1,
            )
        ],
        raw_response={"private": "do-not-return"},
    )


async def _create_source(
    tmp_path,
    *,
    task_id="T-CHECKLIST-API",
    status="generating_checklist",
    parse_status="succeeded",
    current_generation_id=None,
    with_interpret=True,
):
    tender_markdown = "# 资格要求\n必须提交营业执照。"
    tender_md_path = tmp_path / f"{task_id}-tender.md"
    interpret_md_path = tmp_path / f"{task_id}-interpret.md"
    tender_md_path.write_text(tender_markdown, encoding="utf-8")
    if with_interpret:
        interpret_md_path.write_text("# 解读\n核验资格。", encoding="utf-8")
    file_id = f"F-{task_id}"
    task = DiagnosisTask(
        id=task_id,
        tender_filename="tender.pdf",
        tender_path=str(tmp_path / f"{task_id}-tender.pdf"),
        bid_filename="bid.pdf",
        bid_path=str(tmp_path / f"{task_id}-bid.pdf"),
        tender_file_id=file_id,
        status=status,
        config_snapshot=json.dumps([{"id": 7, "title": "资格"}]),
        interpret_md_path=str(interpret_md_path) if with_interpret else None,
        current_checklist_generation_id=current_generation_id,
    )
    workspace_file = WorkspaceFile(
        id=file_id,
        task_id=task_id,
        label="招标文件",
        original_filename="tender.pdf",
        stored_path=task.tender_path,
        kind="document",
        ext=".pdf",
        parse_status=parse_status,
        md_path=str(tender_md_path),
    )
    async with db.SessionLocal() as session:
        session.add_all([task, workspace_file])
        await session.commit()
    return tender_markdown


@pytest.mark.asyncio
async def test_get_checklist_returns_nested_report_without_private_raw_data(
    client, tmp_path
):
    tender = await _create_source(tmp_path)
    generation_id = await ChecklistService(StaticAgent(_draft(tender))).generate_for_task(
        "T-CHECKLIST-API"
    )

    response = await client.get("/api/tasks/T-CHECKLIST-API/checklist")

    assert response.status_code == 200
    body = response.json()
    assert body["generation"] == {
        "id": generation_id,
        "status": "succeeded",
        "agent_type": "test",
        "agent_version": "2026.1",
        "schema_version": "1",
        "error_message": None,
        "created_at": body["generation"]["created_at"],
        "finished_at": body["generation"]["finished_at"],
    }
    assert body["summary"] == {
        "category_count": 1,
        "item_count": 1,
        "importance_counts": {"high": 1, "medium": 0, "low": 0},
    }
    category = body["categories"][0]
    assert category["expected_locations"] == ["资格审查"]
    item = category["items"][0]
    assert item["source_references"][0]["segment_index"] == 0
    assert item["retrieval_hints"] == ["营业执照"]
    assert item["expected_evidence"] == ["有效营业执照"]
    assert item["compliance_rules"] == {"satisfied": "材料完整"}
    assert item["consequence_rules"] == {"bid_unusable": "资格审查不通过"}
    assert item["admin_config_refs"] == [7]
    rendered = json.dumps(body, ensure_ascii=False)
    assert "raw_response_path" not in rendered
    assert "do-not-return" not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_id", "expected_detail"),
    [
        ("missing", "Task not found"),
        ("T-OLD", "Checklist not available"),
    ],
)
async def test_get_checklist_distinguishes_missing_and_unavailable(
    client, tmp_path, task_id, expected_detail
):
    if task_id == "T-OLD":
        await _create_source(tmp_path, task_id=task_id, status="completed")

    response = await client.get(f"/api/tasks/{task_id}/checklist")

    assert response.status_code == 404
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_get_checklist_corrupt_json_returns_safe_fixed_error(client, tmp_path):
    tender = await _create_source(tmp_path)
    await ChecklistService(StaticAgent(_draft(tender))).generate_for_task(
        "T-CHECKLIST-API"
    )
    async with db.SessionLocal() as session:
        item = (await session.scalars(select(ChecklistItem))).one()
        item.source_references = '["secret-corrupt-value"'
        await session.commit()

    response = await client.get("/api/tasks/T-CHECKLIST-API/checklist")

    assert response.status_code == 500
    assert response.json()["detail"] == "checklist_data_invalid"
    assert "secret-corrupt-value" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_status", "parse_status", "with_interpret", "has_current"),
    [
        ("failed", "failed", True, False),
        ("failed", "partial", True, False),
        ("failed", "pending", True, False),
        ("failed", "succeeded", False, False),
        ("completed", "succeeded", True, False),
        ("failed", "succeeded", True, True),
    ],
)
async def test_retry_checklist_rejects_invalid_prerequisites(
    client,
    tmp_path,
    monkeypatch,
    task_status,
    parse_status,
    with_interpret,
    has_current,
):
    await _create_source(
        tmp_path,
        status=task_status,
        parse_status=parse_status,
        with_interpret=with_interpret,
    )
    if has_current:
        tender = "# 资格要求\n必须提交营业执照。"
        await ChecklistService(StaticAgent(_draft(tender))).generate_for_task(
            "T-CHECKLIST-API"
        )
        async with db.SessionLocal() as session:
            task = await session.get(DiagnosisTask, "T-CHECKLIST-API")
            task.status = "failed"
            await session.commit()
    monkeypatch.setattr("app.services.scheduler.start_task", lambda task_id: None)

    response = await client.post("/api/tasks/T-CHECKLIST-API/checklist/retry")

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_retry_checklist_missing_task_is_404(client):
    response = await client.post("/api/tasks/missing/checklist/retry")

    assert response.status_code == 404
    assert response.json()["detail"] == "Task not found"


@pytest.mark.asyncio
async def test_retry_checklist_calls_scheduler_and_returns_accepted(
    client, tmp_path, monkeypatch
):
    await _create_source(tmp_path, status="failed")
    calls = []

    async def fake_retry(task_id):
        calls.append(task_id)

    monkeypatch.setattr("app.services.scheduler.retry_checklist", fake_retry)

    response = await client.post("/api/tasks/T-CHECKLIST-API/checklist/retry")

    assert response.status_code == 202
    assert response.json() == {
        "task_id": "T-CHECKLIST-API",
        "status": "generating_checklist",
    }
    assert calls == ["T-CHECKLIST-API"]


@pytest.mark.asyncio
async def test_retry_checklist_generates_report_end_to_end(client, tmp_path):
    from app.services import scheduler

    await _create_source(tmp_path, status="failed")
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "T-CHECKLIST-API")
        task.error_message = "checklist_generation_failed"
        task.finished_at = task.updated_at
        await session.commit()

    response = await client.post("/api/tasks/T-CHECKLIST-API/checklist/retry")

    assert response.status_code == 202
    for _ in range(100):
        report = await client.get("/api/tasks/T-CHECKLIST-API/checklist")
        if report.status_code == 200:
            break
        await asyncio.sleep(0.01)
    assert report.status_code == 200
    await asyncio.wait_for(
        scheduler._get_control("T-CHECKLIST-API").done_event.wait(),
        timeout=5,
    )
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "T-CHECKLIST-API")
    assert task.current_checklist_generation_id is not None
    assert task.status == "diagnosing"
    assert task.error_message is None
    assert task.finished_at is None


@pytest.mark.asyncio
async def test_retry_checklist_agent_failure_marks_task_failed_safely(
    client, tmp_path, monkeypatch
):
    from app.services import scheduler

    class FailingChecklistAgent:
        async def generate(self, *, task_id, context):
            del task_id, context
            raise RuntimeError("secret-token /Users/private/checklist.json")

    await _create_source(tmp_path, status="failed")
    monkeypatch.setattr(
        scheduler,
        "MockChecklistAgent",
        FailingChecklistAgent,
        raising=False,
    )

    response = await client.post("/api/tasks/T-CHECKLIST-API/checklist/retry")

    assert response.status_code == 202
    status = await scheduler.wait_for_terminal("T-CHECKLIST-API", timeout=5)
    assert status == "failed"
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "T-CHECKLIST-API")
    assert task.current_checklist_generation_id is None
    assert task.error_message == "checklist_generation_failed"


@pytest.mark.asyncio
async def test_scheduler_retry_prepares_state_and_starts_task(
    client, tmp_path, monkeypatch
):
    del client
    from app.services import scheduler

    await _create_source(tmp_path, status="failed")
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "T-CHECKLIST-API")
        task.error_message = "checklist_generation_failed"
        task.finished_at = task.updated_at
        await session.commit()

    await scheduler.retry_checklist("T-CHECKLIST-API")
    await asyncio.wait_for(
        scheduler._get_control("T-CHECKLIST-API").done_event.wait(),
        timeout=5,
    )

    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "T-CHECKLIST-API")
    assert task.status == "diagnosing"
    assert task.current_checklist_generation_id is not None
    assert task.error_message is None
    assert task.finished_at is None


def test_result_out_defaults_new_fields_for_legacy_rows():
    model = ResultOut.model_validate(
        {
            "id": 1,
            "task_id": "T-1",
            "config_id": None,
            "content_title": "旧结果",
            "description": "",
            "result": "ok",
            "evidence": "",
            "suggestion": "",
            "sort_order": 0,
            "created_at": "2026-01-01T00:00:00Z",
        }
    )

    assert model.checklist_item_id is None
    assert model.compliance_status is None
    assert model.consequence_tags == []
    assert model.consequence_tags is not ResultOut.model_validate(
        {
            "id": 2,
            "task_id": "T-1",
            "config_id": None,
            "content_title": "旧结果2",
            "description": "",
            "result": "ok",
            "evidence": "",
            "suggestion": "",
            "sort_order": 1,
            "created_at": "2026-01-01T00:00:00Z",
        }
    ).consequence_tags
