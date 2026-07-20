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


SYSTEM_INSTRUCTIONS = """你是招标诊断检查项生成助手。
根据解读报告、管理端配置参考与当前招标正文分片，生成本分片的近终态检查项清单。
规则：
1. 只基于本分片招标正文生成可追溯检查项；禁止无招标依据的推断。
2. 每条检查项只描述一个可独立判断的要点，填写 title/requirement/technique/importance。
3. importance 只能是 high|medium|low。
4. compliance_rules 必须包含键：satisfied, violated, cannot_satisfy, insufficient_evidence；satisfied、violated 须为非空字符串；cannot_satisfy、insufficient_evidence 若无适用情形可留空；diagnosis_mode=offline 的检查项，insufficient_evidence 须写明线下核验时证据不足的表现（如「电子版无法确认密封/签章/递交等形式要件，需人工现场核验」）。
5. consequence_rules 的键只能来自：no_score, bid_unusable, score_risk, general_risk。
6. source_references 必须含 coordinate_space=segment、segment_index、start、end、section，且偏移落在本分片内。
7. 按预计命中的标书内容位置动态分类，输出 categories 与 items。
8. schema_version 必须为 "1"。
9. diagnosis_mode 只能是 file|offline；涉及装订/打印/密封/盖章/现场递交等需线下核验的检查项标为 offline，其余为 file；offline 项须在 compliance_rules.insufficient_evidence 中说明电子版证据局限性。
10. 去重与合并：同一可独立判断的合规要点（如相同填写格式、同一数值精度要求）在正文多处出现时，只生成一条检查项；title 与 requirement 使用统一、规范的表述（便于跨分片合并）；source_references 可列多处出处，禁止因章节或表述角度不同而拆成语义重复的多条。
11. 得分点细化：评标/评分/价格分等可量化打分内容，须拆到可独立判断的得分点或扣分点；禁止把整章评分标准合并成一条笼统项。但同一评分项下属于同一套计算公式或判定机制的子规则（如价格分中的精度保留、高价惩罚系数、低价优惠系数共同构成一次价格得分计算），应合并为一条检查项，禁止机械拆成多条语义关联的子项。
12. 得分点检查项规范：title 须体现「评分大类·具体得分点」（合并项可用概括性名称，如「价格分·综合折扣率与价格得分计算」）；requirement 须写明满分/权重（若原文有）、满足得分的条件、失分/扣分情形（合并项须完整列出公式、系数、精度等全部子规则）；与评分相关的 consequence_rules 须标注 no_score 或 score_risk，并在规则文字中说明分值影响；technique 说明如何在标书中定位该得分点的响应材料。
13. 非评分类合规要点仍按「一条一要点」拆分，但与得分点不得混并在同一条中。
14. 可参考解读报告中的评分细则理解结构，但每条检查项必须能在本分片正文中追溯；解读报告仅作背景参考。
输出必须是符合 schema 的 JSON 对象。"""


@dataclass(frozen=True)
class ChecklistCallInput:
    system_instructions: str
    interpret_report: str
    admin_config: str
    tender_segment: str
    segment_index: int


@dataclass(frozen=True)
class PromptContext:
    system_instructions: str
    interpret_report: str
    admin_config: str
    segments: list[str]
    calls: list[ChecklistCallInput]


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
    calls = [
        ChecklistCallInput(
            system_instructions=SYSTEM_INSTRUCTIONS,
            interpret_report=interpret_markdown,
            admin_config=config_json,
            tender_segment=segment,
            segment_index=index,
        )
        for index, segment in enumerate(segments)
    ]
    return PromptContext(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report=interpret_markdown,
        admin_config=config_json,
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
