from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, KnowledgeChunk, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from app.services.retrieval.persist import load_chunk_text
from app.services.retrieval.segments import materialize_segments
from app.services.retrieval.table_text import (
    html_table_to_text,
    merge_table_text_into_segments,
)
from tests.stubs.retrieval_ai import apply_retrieval_ai_stubs


def test_html_table_to_text_flattens_cells():
    html = "<table><tr><td>授权单位</td><td>某某公司</td></tr></table>"
    text = html_table_to_text(html)
    assert "授权单位" in text
    assert "某某公司" in text


def test_merge_table_text_appends_to_containing_segment():
    markdown = (
        "# 资质说明\n\n"
        "## 表格章节\n\n"
        "正文段落。\n\n"
        "<!-- table:tbl_001 -->\n"
    )
    tree = build_document_tree(markdown)
    fine_chunks = chunk_from_tree(markdown, tree)
    segments = materialize_segments(markdown, tree, fine_chunks)

    html = "<table><tr><td>资质等级</td><td>甲级</td></tr></table>"
    segments = merge_table_text_into_segments(
        markdown,
        tree,
        segments,
        _FakeTableDir({"tbl_001": html}),
    )

    fine = next(seg for seg in segments if seg.segment_level == "fine")
    assert "资质等级" in fine.text
    assert "甲级" in fine.text

    large = next(seg for seg in segments if seg.segment_level == "large")
    assert "资质等级" in large.text


def test_merge_table_text_creates_orphan_fine_when_unplaced():
    markdown = "<!-- table:tbl_002 -->\n"
    tree = {"nodes": [{"id": "n_root", "title": "根", "start_offset": 0, "end_offset": 30, "subtree_end": 30, "children": []}]}
    segments: list = []

    html = "<table><tr><td>独立表格</td></tr></table>"
    segments = merge_table_text_into_segments(
        markdown,
        tree,
        segments,
        _FakeTableDir({"tbl_002": html}),
    )

    assert len(segments) == 1
    assert segments[0].source == "table"
    assert segments[0].chunk_id == "tbl_tbl_002"
    assert "独立表格" in segments[0].text


class _FakeTableDir:
    def __init__(self, tables: dict[str, str]) -> None:
        self._tables = tables

    def is_dir(self) -> bool:
        return True

    def __truediv__(self, name: str) -> Path:
        return _FakeTablePath(name, self._tables)


class _FakeTablePath:
    def __init__(self, name: str, tables: dict[str, str]) -> None:
        self.name = name
        self._tables = tables

    def is_file(self) -> bool:
        return self.name in self._tables or self.name.removesuffix(".html") in self._tables

    def read_text(self, encoding: str = "utf-8") -> str:
        if self.name in self._tables:
            return self._tables[self.name]
        return self._tables[self.name.removesuffix(".html")]


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "table_index.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("app.config.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.artifact.UPLOAD_DIR", upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    apply_retrieval_ai_stubs(monkeypatch)

    await index_scheduler.reset_for_tests()

    async with session_factory() as session:
        yield session

    await index_scheduler.reset_for_tests()
    await engine.dispose()


@pytest.mark.asyncio
async def test_index_job_includes_table_text(db_session, tmp_path, monkeypatch):
    task_id = "T-TABLE-IDX"
    file_id = "ftbl001"
    md_text = (
        "# 资质说明\n\n"
        "正文段落。\n\n"
        "<!-- table:tbl_001 -->\n"
    )

    db_session.add(
        DiagnosisTask(
            id=task_id,
            tender_filename="tender.docx",
            tender_path="/tmp/tender.docx",
            bid_filename="bid.docx",
            bid_path="/tmp/bid.docx",
            config_snapshot="[]",
        )
    )

    tree = build_document_tree(md_text)
    fine_chunks = chunk_from_tree(md_text, tree)
    root = artifact.ensure_artifact_dirs(task_id)
    md_path = root / "markdown" / f"{file_id}.md"
    tree_path = root / "json" / f"{file_id}.tree.json"
    chunks_path = root / "json" / f"{file_id}.chunks.json"
    table_dir = root / "table" / file_id
    table_dir.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")
    tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    chunks_path.write_text(json.dumps(fine_chunks, ensure_ascii=False), encoding="utf-8")
    (table_dir / "tbl_001.html").write_text(
        "<table><tr><td>资质等级</td><td>甲级</td></tr></table>",
        encoding="utf-8",
    )

    wf = WorkspaceFile(
        id=file_id,
        task_id=task_id,
        label="资质文件",
        original_filename="sample.docx",
        stored_path=str(root / "document" / f"{file_id}.docx"),
        kind="document",
        ext=".docx",
        parse_status="succeeded",
        md_path=str(md_path),
        tree_path=str(tree_path),
        chunks_path=str(chunks_path),
    )
    db_session.add(wf)
    await db_session.commit()

    await index_scheduler.enqueue(task_id, file_id)
    await index_scheduler.drain_once_for_tests()

    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id)
        )
    ).scalars().all()
    assert any("资质等级" in load_chunk_text(chunk) for chunk in chunks)
