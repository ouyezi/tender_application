from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DiagnosisConfig(Base):
    __tablename__ = "diagnosis_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    technique: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_mode: Mapped[str] = mapped_column(String(32), nullable=False)  # full_text | description
    content_scope: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DiagnosisTask(Base):
    __tablename__ = "diagnosis_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tender_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    tender_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    bid_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    bid_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    tender_file_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    bid_file_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    background: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="interpreting")
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    config_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    report_md_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    report_docx_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    interpret_md_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    interpret_html_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    current_checklist_generation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["DiagnosisResult"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class ChecklistGeneration(Base):
    __tablename__ = "checklist_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("diagnosis_tasks.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_config_snapshot: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]"
    )
    raw_response_path: Mapped[Optional[str]] = mapped_column(
        String(1024), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ChecklistCategory(Base):
    __tablename__ = "checklist_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_locations: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=False, index=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("checklist_categories.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    technique: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[str] = mapped_column(String(16), nullable=False)
    source_references: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_hints: Mapped[str] = mapped_column(Text, nullable=False)
    expected_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    compliance_rules: Mapped[str] = mapped_column(Text, nullable=False)
    consequence_rules: Mapped[str] = mapped_column(Text, nullable=False)
    admin_config_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    content_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="precise_search", server_default="precise_search"
    )
    content_target: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default="{}"
    )
    diagnosis_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="file", server_default="file"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class DiagnosisResult(Base):
    __tablename__ = "diagnosis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("diagnosis_tasks.id"), nullable=False, index=True)
    config_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    checklist_item_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("checklist_items.id"), nullable=True, index=True
    )
    content_title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    compliance_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    consequence_tags: Mapped[str] = mapped_column(
        Text, nullable=False, default="[]", server_default="[]"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped["DiagnosisTask"] = relationship(back_populates="results")


class WorkspaceFile(Base):
    __tablename__ = "workspace_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("diagnosis_tasks.id"), index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="document")  # document | other
    ext: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending | running | succeeded | failed | partial | skipped
    parse_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tree_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    md_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    chunks_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ParseJob(Base):
    __tablename__ = "parse_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(32), ForeignKey("workspace_files.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # queued | running | succeeded | failed
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="convert")
    # convert | extract | build_tree | chunk | write_index
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    warnings: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeTag(Base):
    __tablename__ = "knowledge_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    file_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ancestor_node_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    segment_level: Mapped[str] = mapped_column(String(16), nullable=False)  # fine|large
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    title_path: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    text_inline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    child_chunk_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="native_text")
    document_role: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    index_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    embedding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    member_chunk_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IndexJob(Base):
    __tablename__ = "index_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    file_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # queued|running|partial|ready|failed
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="segments")
    # segments|enrich|fts|vectors|wiki
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
