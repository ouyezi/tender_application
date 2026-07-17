from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.services.checklist_context import PromptContext


@dataclass
class DiagnosisItemResult:
    content_title: str
    description: str
    result: str
    evidence: str
    suggestion: str
    config_id: int | None = None


class DiagnosisEngine(Protocol):
    async def diagnose_item(
        self,
        task_id: str,
        config_item: dict[str, Any],
        documents: dict[str, str],
    ) -> DiagnosisItemResult: ...


@dataclass
class InterpretationResult:
    markdown: str
    title: str = "招标文件解读报告"


class InterpretationAgent(Protocol):
    async def interpret(
        self,
        *,
        task_id: str,
        tender_path: str,
        background: str,
    ) -> InterpretationResult: ...


@dataclass(frozen=True)
class ChecklistCategoryDraft:
    id: str
    name: str
    description: str
    retrieval_query: str
    expected_locations: list[str]
    sort_order: int


@dataclass(frozen=True)
class ChecklistItemDraft:
    id: str
    category_id: str
    title: str
    requirement: str
    technique: str
    importance: str
    source_references: list[dict[str, Any]]
    retrieval_hints: list[str]
    expected_evidence: list[str]
    compliance_rules: dict[str, str]
    consequence_rules: dict[str, str]
    admin_config_refs: list[int]
    sort_order: int


@dataclass(frozen=True)
class ChecklistDraft:
    schema_version: str
    categories: list[ChecklistCategoryDraft]
    items: list[ChecklistItemDraft]
    raw_response: dict[str, Any]


class ChecklistAgent(Protocol):
    async def generate(
        self,
        *,
        task_id: str,
        context: PromptContext,
    ) -> ChecklistDraft: ...
