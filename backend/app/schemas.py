from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ContentMode = Literal["full_text", "description"]
Importance = Literal["high", "medium", "low"]


class ConfigCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    technique: str = ""
    content_mode: ContentMode
    content_scope: Optional[str] = Field(None, max_length=64)
    content_text: Optional[str] = None
    importance: Importance = "medium"


class ConfigUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    technique: str = ""
    content_mode: ContentMode
    content_scope: Optional[str] = Field(None, max_length=64)
    content_text: Optional[str] = None
    importance: Importance = "medium"


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    technique: str
    content_mode: str
    content_scope: Optional[str]
    content_text: Optional[str]
    importance: str
    created_at: datetime
    updated_at: datetime


class ResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: str
    config_id: Optional[int]
    checklist_item_id: Optional[str] = None
    content_title: str
    description: str
    result: str
    evidence: str
    suggestion: str
    response_content: str = ""
    compliance_status: Optional[str] = None
    consequence_tags: List[str] = Field(default_factory=list)
    sort_order: int
    created_at: datetime

    @field_validator("consequence_tags", mode="before")
    @classmethod
    def deserialize_consequence_tags(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid consequence tags") from exc
        if not isinstance(decoded, list) or any(
            not isinstance(tag, str) for tag in decoded
        ):
            raise ValueError("invalid consequence tags")
        return decoded


class ChecklistItemOut(BaseModel):
    id: str
    title: str
    requirement: str
    technique: str
    importance: str
    source_references: List[dict]
    retrieval_hints: List[str]
    expected_evidence: List[str]
    compliance_rules: dict[str, str]
    consequence_rules: dict[str, str]
    admin_config_refs: List[int]
    content_source: str = "precise_search"
    content_target: dict = {}
    diagnosis_mode: str = "file"
    sort_order: int


class ChecklistCategoryOut(BaseModel):
    id: str
    name: str
    description: str
    retrieval_query: str
    expected_locations: List[str]
    sort_order: int
    items: List[ChecklistItemOut]


class ChecklistGenerationOut(BaseModel):
    id: int
    status: str
    agent_type: str
    agent_version: str
    schema_version: str
    error_message: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]


class ChecklistSummaryOut(BaseModel):
    category_count: int
    item_count: int
    importance_counts: dict[str, int]


class TaskReadinessOut(BaseModel):
    checklist_ready: bool
    bid_index_ready: bool
    bid_index_required: bool
    diagnosis_ready: bool
    checklist_lane_active: bool
    bid_index_lane_active: bool
    full_run_active: bool
    diagnosis_lane_active: bool


class ChecklistReportOut(BaseModel):
    generation: ChecklistGenerationOut
    summary: ChecklistSummaryOut
    categories: List[ChecklistCategoryOut]


class TaskListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tender_filename: str
    bid_filename: str
    background: str
    requirements: str
    status: str
    progress_done: int
    progress_total: int
    report_md_path: Optional[str]
    report_docx_path: Optional[str]
    interpret_md_path: Optional[str]
    interpret_html_path: Optional[str]
    current_checklist_generation_id: Optional[int] = None
    error_message: Optional[str]
    failure_stage: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime]


class TaskOut(TaskListOut):
    tender_path: str
    bid_path: str
    results: List[ResultOut] = []
    report_markdown: str = ""
    interpret_markdown: str = ""
    readiness: Optional[TaskReadinessOut] = None


class WorkspaceFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    label: str
    original_filename: str
    kind: str
    ext: str
    parse_status: str
    parse_error: Optional[str]
    created_at: datetime
    updated_at: datetime


class WorkspaceListItem(BaseModel):
    task_id: str
    tender_filename: str
    bid_filename: str
    file_count: int
    parse_succeeded: int
    parse_running: int
    parse_failed: int
    created_at: datetime


class WorkspaceDetailOut(BaseModel):
    task_id: str
    tender_filename: str
    bid_filename: str
    files: List[WorkspaceFileOut]


class TreeNodeOut(BaseModel):
    id: str
    title: str
    level: int
    numbering: str = ""
    parent_id: Optional[str] = None
    start_offset: int
    end_offset: int
    self_start: int
    subtree_end: int
    source: str = "heading"
    children: List["TreeNodeOut"] = Field(default_factory=list)


TreeNodeOut.model_rebuild()


class ContentOut(BaseModel):
    node_id: str
    title: str
    markdown: str
    # 当前返回正文切片的起止（含子孙：self_start → subtree_end）
    start_offset: int = 0
    end_offset: int = 0
    self_start: int = 0
    subtree_end: int = 0
    # 仅本节正文（不含子章节）的起止，便于对照
    section_start: int = 0
    section_end: int = 0


class ExecutionNodeOut(BaseModel):
    id: str
    key: str
    label: str
    kind: str
    status: str
    parent_key: Optional[str] = None
    sort_order: int = 0
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ExecutionEdgeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_key: str = Field(alias="from")
    to_key: str = Field(alias="to")
    kind: str


class ExecutionGraphSummaryOut(BaseModel):
    total_nodes: int
    completed: int
    running: int
    failed: int
    pending: int
    total_duration_ms: int


class ExecutionGraphOut(BaseModel):
    task_id: str
    task_status: str
    is_terminal: bool
    legacy: bool
    summary: ExecutionGraphSummaryOut
    nodes: list[ExecutionNodeOut]
    edges: list[ExecutionEdgeOut]
