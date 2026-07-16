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
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["DiagnosisResult"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class DiagnosisResult(Base):
    __tablename__ = "diagnosis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("diagnosis_tasks.id"), nullable=False, index=True)
    config_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped["DiagnosisTask"] = relationship(back_populates="results")
