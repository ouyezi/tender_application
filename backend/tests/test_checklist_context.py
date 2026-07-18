import json
import threading
from bisect import bisect_right as standard_bisect_right
from pathlib import Path

import pytest

from app.models import DiagnosisTask, WorkspaceFile
from app.services import checklist_context
from app.services.checklist_context import (
    ChecklistInputError,
    build_prompt_context,
    estimate_tokens,
    load_task_source,
    split_tender_markdown,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", 0),
        ("abcd", 1),
        ("abcde", 2),
        ("中文", 2),
        ("中文abcdx", 4),
        ("，Ａ🙂", 3),
        ("a，bＡ🙂", 4),
    ],
)
def test_estimate_tokens_counts_cjk_and_ascii_predictably(text, expected):
    assert estimate_tokens(text) == expected


def test_short_document_returns_original_single_segment():
    markdown = "# 招标公告\n\n短正文"

    segments = split_tender_markdown(markdown, 100, 50, 5)

    assert segments == [markdown]


def test_long_document_builds_explicit_calls_in_stable_order():
    tender = "# 第一章\n" + "甲" * 80 + "\n# 第二章\n" + "乙" * 80
    interpretation = "# 解读报告\n完整解读"
    configs = [{"title": "后项", "id": 2}, {"id": 1, "title": "前项"}]

    context = build_prompt_context(tender, interpretation, configs, 20, 14, 2)

    assert len(context.segments) > 1
    assert [call.tender_segment for call in context.calls] == context.segments
    assert context.system_instructions == checklist_context.SYSTEM_INSTRUCTIONS
    assert context.interpret_report == interpretation
    expected_config = json.dumps(
        configs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert context.admin_config == expected_config
    for index, call in enumerate(context.calls):
        assert call.system_instructions == context.system_instructions
        assert call.interpret_report == interpretation
        assert call.admin_config == expected_config
        assert call.segment_index == index
    assert tender not in context.system_instructions


def test_multiple_atx_sections_split_at_heading_boundaries_first():
    first = "# A\n" + "a" * 12 + "\n"
    second = " ## B\n" + "b" * 12 + "\n"
    third = "   # C\n" + "c" * 12 + "\n"
    markdown = first + second + third

    segments = split_tender_markdown(markdown, 1, 10, 0)

    assert segments == [first + second, third]
    assert all(estimate_tokens(segment) <= 10 for segment in segments)


def test_many_headings_use_bisect_instead_of_rescanning(monkeypatch):
    sections = [f"## Section {index}\n正文{index}\n" for index in range(2_000)]
    markdown = "".join(sections)
    bisect_calls = 0

    def tracking_bisect_right(offsets, value, low=0, high=None):
        nonlocal bisect_calls
        bisect_calls += 1
        if high is None:
            return standard_bisect_right(offsets, value, low)
        return standard_bisect_right(offsets, value, low, high)

    monkeypatch.setattr(
        checklist_context,
        "bisect_right",
        tracking_bisect_right,
        raising=False,
    )

    segments = split_tender_markdown(markdown, 1, 40, 0)

    assert "".join(segments) == markdown
    assert all(estimate_tokens(segment) <= 40 for segment in segments)
    assert bisect_calls == len(segments)


def test_single_oversized_section_is_split_with_overlap_without_losing_content():
    markdown = "# 超长章节\n" + "".join(chr(0x4E00 + index) for index in range(80))

    segments = split_tender_markdown(markdown, 10, 8, 2)

    assert len(segments) > 1
    for left, right in zip(segments, segments[1:]):
        assert left[-2:] == right[:2]
        assert estimate_tokens(right[:2]) <= 2
    assert all(estimate_tokens(segment) <= 8 for segment in segments)
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
    workspace_task_id=None,
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
        task_id=workspace_task_id or task_id,
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
async def test_load_task_source_reads_files_off_thread_after_session_closes(
    tmp_path, client, monkeypatch
):
    from app import db

    await _create_task_source(tmp_path)
    original_session_factory = db.SessionLocal
    original_read_text = Path.read_text
    session_open = False
    read_threads = []
    main_thread = threading.get_ident()

    class TrackingSessionContext:
        async def __aenter__(self):
            nonlocal session_open
            self.context = original_session_factory()
            session = await self.context.__aenter__()
            session_open = True
            return session

        async def __aexit__(self, *args):
            nonlocal session_open
            result = await self.context.__aexit__(*args)
            session_open = False
            return result

    def tracked_read_text(path, *args, **kwargs):
        assert not session_open
        read_threads.append(threading.get_ident())
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(db, "SessionLocal", TrackingSessionContext)
    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    await load_task_source("T-CONTEXT")

    assert len(read_threads) == 2
    assert all(thread_id != main_thread for thread_id in read_threads)


@pytest.mark.asyncio
async def test_load_task_source_rejects_tender_from_another_task(tmp_path, client):
    await _create_task_source(tmp_path, workspace_task_id="T-OTHER")

    with pytest.raises(ChecklistInputError, match="tender_file_task_mismatch"):
        await load_task_source("T-CONTEXT")


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
