from __future__ import annotations

import asyncio
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import db
from app.models import DiagnosisTask, WorkspaceFile


class ChecklistInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptCall:
    stable_prefix: str
    tender_segment: str


@dataclass(frozen=True)
class PromptContext:
    stable_prefix: str
    segments: list[str]
    calls: list[PromptCall]


def estimate_tokens(text: str) -> int:
    """Estimate non-ASCII as one token each and ASCII as four chars per token."""
    ascii_count = sum(1 for char in text if char.isascii())
    non_ascii_count = len(text) - ascii_count
    return non_ascii_count + (ascii_count + 3) // 4


def _max_prefix_end(text: str, start: int, token_budget: int) -> int:
    """Return the furthest end whose slice from start fits the token budget."""
    low = start
    high = min(len(text), start + token_budget * 4) + 1
    while low + 1 < high:
        middle = (low + high) // 2
        if estimate_tokens(text[start:middle]) <= token_budget:
            low = middle
        else:
            high = middle
    return low


def _max_suffix_start(text: str, end: int, token_budget: int) -> int:
    """Return the earliest start whose slice to end fits the token budget."""
    low = max(0, end - token_budget * 4)
    high = end
    while low < high:
        middle = (low + high) // 2
        if estimate_tokens(text[middle:end]) <= token_budget:
            high = middle
        else:
            low = middle + 1
    return low


def _validate_split_parameters(
    threshold_tokens: int,
    chunk_tokens: int,
    overlap_tokens: int,
) -> None:
    if threshold_tokens <= 0:
        raise ValueError("threshold_tokens must be greater than zero")
    if chunk_tokens <= 0:
        raise ValueError("chunk_tokens must be greater than zero")
    if overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("overlap_tokens must be non-negative and less than chunk_tokens")


def split_tender_markdown(
    markdown: str,
    threshold_tokens: int,
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    _validate_split_parameters(threshold_tokens, chunk_tokens, overlap_tokens)
    if not markdown or estimate_tokens(markdown) <= threshold_tokens:
        return [markdown]

    heading_offsets = [
        match.start()
        for match in re.finditer(r"(?m)^ {0,3}#{1,6}[ \t]+", markdown)
        if match.start() > 0
    ]
    segments: list[str] = []
    start = 0
    frontier = 0
    text_length = len(markdown)
    while frontier < text_length:
        limit = _max_prefix_end(markdown, start, chunk_tokens)
        boundary_index = bisect_right(heading_offsets, limit) - 1
        if boundary_index >= 0 and heading_offsets[boundary_index] > frontier:
            end = heading_offsets[boundary_index]
        else:
            end = limit
        segments.append(markdown[start:end])
        frontier = end
        if frontier == text_length:
            break
        start = _max_suffix_start(markdown, frontier, overlap_tokens)
    return segments


def build_prompt_context(
    tender_markdown: str,
    interpret_markdown: str,
    admin_configs: list[Any],
    threshold_tokens: int,
    chunk_tokens: int,
    overlap_tokens: int,
) -> PromptContext:
    segments = split_tender_markdown(
        tender_markdown,
        threshold_tokens,
        chunk_tokens,
        overlap_tokens,
    )
    config_json = json.dumps(
        admin_configs,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    stable_prefix = (
        "## 固定生成规则和 schema\n"
        "根据完整输入生成结构化检查项；输出必须符合检查项 schema。\n\n"
        "## 完整解读报告\n"
        f"{interpret_markdown}\n\n"
        "## 管理端配置（JSON，稳定 key 排序）\n"
        f"{config_json}\n\n"
        "## 当前招标正文分片\n"
        "正文分片由每次调用的 tender_segment 单独提供。"
    )
    calls = [
        PromptCall(stable_prefix=stable_prefix, tender_segment=segment)
        for segment in segments
    ]
    return PromptContext(
        stable_prefix=stable_prefix,
        segments=segments,
        calls=calls,
    )


async def _read_markdown(path_value: str | None, missing_error: str) -> str:
    if not path_value:
        raise ChecklistInputError(missing_error)
    path = Path(path_value)
    try:
        return await asyncio.to_thread(path.read_text, encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise ChecklistInputError(missing_error) from exc
    except (OSError, UnicodeError) as exc:
        raise ChecklistInputError(f"{missing_error}_unreadable") from exc


async def load_task_source(
    task_id: str,
) -> tuple[DiagnosisTask, str, str, list[Any]]:
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise ChecklistInputError("task_missing")

        workspace_file = None
        if task.tender_file_id:
            workspace_file = await session.get(WorkspaceFile, task.tender_file_id)
        if workspace_file is None:
            raise ChecklistInputError("tender_parse_missing")
        if workspace_file.task_id != task.id:
            raise ChecklistInputError("tender_file_task_mismatch")
        if workspace_file.parse_status != "succeeded":
            raise ChecklistInputError(
                f"tender_parse_{workspace_file.parse_status or 'missing'}"
            )

        tender_md_path = workspace_file.md_path
        interpret_md_path = task.interpret_md_path
        config_snapshot = task.config_snapshot

    tender_markdown = await _read_markdown(
        tender_md_path,
        "tender_markdown_missing",
    )
    interpret_markdown = await _read_markdown(
        interpret_md_path,
        "interpret_markdown_missing",
    )
    try:
        admin_configs = json.loads(config_snapshot)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChecklistInputError("config_snapshot_invalid") from exc
    if not isinstance(admin_configs, list):
        raise ChecklistInputError("config_snapshot_invalid")

    return task, tender_markdown, interpret_markdown, admin_configs
