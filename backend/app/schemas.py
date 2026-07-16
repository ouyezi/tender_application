from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
    content_title: str
    description: str
    result: str
    evidence: str
    suggestion: str
    sort_order: int
    created_at: datetime


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
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime]


class TaskOut(TaskListOut):
    tender_path: str
    bid_path: str
    results: List[ResultOut] = []
    report_markdown: str = ""
    interpret_markdown: str = ""


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
