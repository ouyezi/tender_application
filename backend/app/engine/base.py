from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    location: str = ""


@dataclass
class RetrievalHit:
    chunk_id: str
    file_id: str
    node_id: str
    segment_level: str
    title: str
    summary: str
    title_path: list[str]
    tags: list[dict]
    text: str = ""
    child_chunk_ids: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class RetrievalResult:
    mode: str
    items: list[RetrievalHit]
    index_status: str  # ready|partial|unavailable
    incomplete: bool = False
    degraded: bool = False
    error: str | None = None


class TypedRetrievalProvider(Protocol):
    async def retrieve(
        self,
        *,
        task_id: str,
        content_source: str,
        content_target: dict[str, Any],
        item_hints: dict[str, Any] | None = None,
    ) -> RetrievalResult: ...


class RetrievalProvider(Protocol):
    async def retrieve_for_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[RetrievedChunk]: ...


@dataclass
class BatchItemResult:
    checklist_item_id: str
    compliance: str  # satisfied | violated | cannot_satisfy | insufficient_evidence
    consequence_tags: list[str]
    evidence: str
    suggestion: str
    description: str = ""


class BatchDiagnosisEngine(Protocol):
    async def diagnose_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[BatchItemResult]: ...
