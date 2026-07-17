from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base, DiagnosisTask, KnowledgeChunk, IndexJob, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree

FIXTURES = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "index_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("app.config.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.artifact.UPLOAD_DIR", upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    await index_scheduler.reset_for_tests()

    async with session_factory() as session:
        yield session

    await index_scheduler.reset_for_tests()
    await engine.dispose()


@pytest_asyncio.fixture
async def sample_parsed_workspace_file(db_session, tmp_path, monkeypatch):
    """WorkspaceFile with md/tree/chunks artifacts on disk."""
    task_id = "T-INDEX-001"
    file_id = "findex001"

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

    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_chunks = chunk_from_tree(md, tree)

    root = artifact.ensure_artifact_dirs(task_id)
    md_path = root / "markdown" / f"{file_id}.md"
    tree_path = root / "json" / f"{file_id}.tree.json"
    chunks_path = root / "json" / f"{file_id}.chunks.json"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_dir = tree_path.parent
    json_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md, encoding="utf-8")
    tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    chunks_path.write_text(json.dumps(fine_chunks, ensure_ascii=False), encoding="utf-8")

    wf = WorkspaceFile(
        id=file_id,
        task_id=task_id,
        label="招标文件",
        original_filename="sample.md",
        stored_path=str(root / "document" / f"{file_id}_sample.docx"),
        kind="document",
        ext=".docx",
        parse_status="succeeded",
        md_path=str(md_path),
        tree_path=str(tree_path),
        chunks_path=str(chunks_path),
    )
    db_session.add(wf)
    await db_session.commit()
    await db_session.refresh(wf)
    return wf


@pytest.mark.asyncio
async def test_index_job_writes_fine_and_large(db_session, sample_parsed_workspace_file):
    """sample_parsed_workspace_file fixture: WorkspaceFile with md/tree/chunks on disk."""
    await index_scheduler.enqueue(
        sample_parsed_workspace_file.task_id,
        sample_parsed_workspace_file.id,
    )
    await index_scheduler.drain_once_for_tests()

    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.file_id == sample_parsed_workspace_file.id
            )
        )
    ).scalars().all()
    assert any(c.segment_level == "fine" for c in chunks)
    assert any(c.segment_level == "large" for c in chunks)
    job = (
        await db_session.execute(
            select(IndexJob).where(IndexJob.file_id == sample_parsed_workspace_file.id)
        )
    ).scalar_one()
    assert job.status in {"ready", "partial"}
