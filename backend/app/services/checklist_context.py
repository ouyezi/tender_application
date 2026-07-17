from __future__ import annotations

import json
import re
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
    """Return a deterministic, dependency-free upper-bound approximation."""
    return len(text)


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
        for match in re.finditer(r"(?m)^#{1,6}[ \t]+", markdown)
        if match.start() > 0
    ]
    segments: list[str] = []
    start = 0
    frontier = 0
    text_length = len(markdown)
    while frontier < text_length:
        limit = min(start + chunk_tokens, text_length)
        section_ends = [
            offset for offset in heading_offsets if frontier < offset <= limit
        ]
        end = max(section_ends) if section_ends else limit
        segments.append(markdown[start:end])
        frontier = end
        if frontier == text_length:
            break
        start = max(0, frontier - overlap_tokens)
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


def _read_markdown(path_value: str | None, missing_error: str) -> str:
    if not path_value:
        raise ChecklistInputError(missing_error)
    path = Path(path_value)
    if not path.is_file():
        raise ChecklistInputError(missing_error)
    try:
        return path.read_text(encoding="utf-8")
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
        if workspace_file.parse_status != "succeeded":
            raise ChecklistInputError(
                f"tender_parse_{workspace_file.parse_status or 'missing'}"
            )

        tender_markdown = _read_markdown(
            workspace_file.md_path,
            "tender_markdown_missing",
        )
        interpret_markdown = _read_markdown(
            task.interpret_md_path,
            "interpret_markdown_missing",
        )
        try:
            admin_configs = json.loads(task.config_snapshot)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ChecklistInputError("config_snapshot_invalid") from exc
        if not isinstance(admin_configs, list):
            raise ChecklistInputError("config_snapshot_invalid")

        return task, tender_markdown, interpret_markdown, admin_configs
