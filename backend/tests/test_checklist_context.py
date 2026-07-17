import json

import pytest

from app.models import DiagnosisTask, WorkspaceFile
from app.services.checklist_context import (
    ChecklistInputError,
    build_prompt_context,
    load_task_source,
    split_tender_markdown,
)


def test_short_document_returns_original_single_segment():
    markdown = "# 招标公告\n\n短正文"

    segments = split_tender_markdown(markdown, 100, 50, 5)

    assert segments == [markdown]


def test_long_document_builds_cache_friendly_calls_in_stable_order():
    tender = "# 第一章\n" + "甲" * 80 + "\n# 第二章\n" + "乙" * 80
    interpretation = "# 解读报告\n完整解读"
    configs = [{"title": "后项", "id": 2}, {"id": 1, "title": "前项"}]

    context = build_prompt_context(tender, interpretation, configs, 20, 14, 2)

    assert len(context.segments) > 1
    assert [call.tender_segment for call in context.calls] == context.segments
    assert {call.stable_prefix for call in context.calls} == {context.stable_prefix}
    prefix = context.stable_prefix
    rules_at = prefix.index("固定生成规则和 schema")
    interpretation_at = prefix.index(interpretation)
    configs_at = prefix.index(
        json.dumps(configs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    segment_heading_at = prefix.index("当前招标正文分片")
    assert rules_at < interpretation_at < configs_at < segment_heading_at
    assert tender not in prefix


def test_single_oversized_section_is_split_with_overlap_without_losing_content():
    markdown = "# 超长章节\n" + "".join(chr(0x4E00 + index) for index in range(80))

    segments = split_tender_markdown(markdown, 10, 8, 2)

    assert len(segments) > 1
    for left, right in zip(segments, segments[1:]):
        assert left[-2:] == right[:2]
    cursor = segments[0]
    for segment in segments[1:]:
        cursor += segment[2:]
    assert cursor == markdown


@pytest.mark.parametrize(
    ("threshold", "chunk", "overlap"),
    [(0, 10, 0), (-1, 10, 0), (10, 0, 0), (10, -1, 0), (10, 10, -1), (10, 10, 10), (10, 10, 11)],
)
def test_invalid_split_parameters_fail_fast(threshold, chunk, overlap):
    with pytest.raises(ValueError):
        split_tender_markdown("正文", threshold, chunk, overlap)


def test_empty_document_returns_one_empty_segment():
    assert split_tender_markdown("", 10, 5, 1) == [""]


async def _create_task_source(
    tmp_path,
    *,
    task_id="T-CONTEXT",
    parse_status="succeeded",
    config_snapshot=None,
    tender_exists=True,
    interpret_exists=True,
):
    from app import db

    tender_path = tmp_path / f"{task_id}-tender.md"
    interpret_path = tmp_path / f"{task_id}-interpret.md"
    if tender_exists:
        tender_path.write_text("# 招标正文\n内容", encoding="utf-8")
    if interpret_exists:
        interpret_path.write_text("# 解读报告\n结论", encoding="utf-8")

    file_id = f"F-{task_id}"
    task = DiagnosisTask(
        id=task_id,
        tender_filename="tender.docx",
        tender_path=str(tmp_path / "raw-tender.docx"),
        bid_filename="bid.docx",
        bid_path=str(tmp_path / "bid.docx"),
        tender_file_id=file_id,
        interpret_md_path=str(interpret_path),
        config_snapshot=(
            config_snapshot
            if config_snapshot is not None
            else json.dumps([{"id": 1, "title": "资格要求"}], ensure_ascii=False)
        ),
    )
    workspace_file = WorkspaceFile(
        id=file_id,
        task_id=task_id,
        label="招标文件",
        original_filename="tender.docx",
        stored_path=str(tmp_path / "raw-tender.docx"),
        kind="document",
        ext=".docx",
        parse_status=parse_status,
        md_path=str(tender_path),
    )
    async with db.SessionLocal() as session:
        session.add_all([task, workspace_file])
        await session.commit()
    return task


@pytest.mark.asyncio
async def test_load_task_source_reads_succeeded_markdown_interpretation_and_config(tmp_path, client):
    await _create_task_source(tmp_path)

    task, tender, interpretation, configs = await load_task_source("T-CONTEXT")

    assert task.id == "T-CONTEXT"
    assert tender == "# 招标正文\n内容"
    assert interpretation == "# 解读报告\n结论"
    assert configs == [{"id": 1, "title": "资格要求"}]


@pytest.mark.asyncio
async def test_load_task_source_rejects_partial_parse(tmp_path, client):
    await _create_task_source(tmp_path, parse_status="partial")

    with pytest.raises(ChecklistInputError, match="tender_parse_partial"):
        await load_task_source("T-CONTEXT")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tender_exists", "interpret_exists", "error"),
    [
        (False, True, "tender_markdown_missing"),
        (True, False, "interpret_markdown_missing"),
    ],
)
async def test_load_task_source_rejects_missing_files(
    tmp_path, client, tender_exists, interpret_exists, error
):
    await _create_task_source(
        tmp_path,
        tender_exists=tender_exists,
        interpret_exists=interpret_exists,
    )

    with pytest.raises(ChecklistInputError, match=error):
        await load_task_source("T-CONTEXT")


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", ["{bad json", '{"not": "a list"}'])
async def test_load_task_source_rejects_invalid_config_snapshot(tmp_path, client, snapshot):
    await _create_task_source(tmp_path, config_snapshot=snapshot)

    with pytest.raises(ChecklistInputError, match="config_snapshot_invalid"):
        await load_task_source("T-CONTEXT")
