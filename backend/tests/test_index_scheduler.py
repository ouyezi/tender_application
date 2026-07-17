from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, KnowledgeChunk, IndexJob, KnowledgeTag, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from app.services.retrieval.persist import load_chunk_text, write_segments
from app.services.retrieval.types import SegmentDraft

FIXTURES = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "index_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    async with session_factory() as seed_session:
        seed_session.add(
            KnowledgeTag(
                name="技术方案",
                aliases=json.dumps(["架构设计"], ensure_ascii=False),
                description="技术响应与方案",
                enabled=1,
            )
        )
        await seed_session.commit()

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
    assert job.status == "ready"
    assert job.stage == "wiki"

    enriched = [
        c
        for c in chunks
        if c.summary and c.description and c.index_status == "ready"
    ]
    assert enriched, "expected at least one enriched knowledge chunk"

    assert any(
        any(tag.get("name") == "技术方案" for tag in json.loads(c.tags or "[]"))
        for c in chunks
    ), "expected controlled tag from catalog matching sample content"


@pytest.mark.asyncio
async def test_index_job_fails_when_artifacts_missing(db_session, sample_parsed_workspace_file):
    sample_parsed_workspace_file.chunks_path = None
    await db_session.commit()

    await index_scheduler.enqueue(
        sample_parsed_workspace_file.task_id,
        sample_parsed_workspace_file.id,
    )
    await index_scheduler.drain_once_for_tests()

    job = (
        await db_session.execute(
            select(IndexJob).where(IndexJob.file_id == sample_parsed_workspace_file.id)
        )
    ).scalar_one()
    assert job.status == "failed"
    assert job.error_message == "parse_artifacts_missing"

    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.file_id == sample_parsed_workspace_file.id
            )
        )
    ).scalars().all()
    assert chunks == []


@pytest.mark.asyncio
async def test_reindex_replaces_chunks_without_duplicating(db_session, sample_parsed_workspace_file):
    for _ in range(2):
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
    chunk_ids = [c.chunk_id for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert any(c.segment_level == "fine" for c in chunks)
    assert any(c.segment_level == "large" for c in chunks)

    jobs = (
        await db_session.execute(
            select(IndexJob)
            .where(IndexJob.file_id == sample_parsed_workspace_file.id)
            .order_by(IndexJob.id.asc())
        )
    ).scalars().all()
    assert len(jobs) == 2
    assert all(job.status == "ready" for job in jobs)


@pytest.mark.asyncio
async def test_load_chunk_text_inline_and_external(db_session, tmp_path, monkeypatch):
    task_id = "T-INDEX-TEXT"
    file_id = "findextext"
    text_dir = tmp_path / "uploads" / task_id / "index_text"
    inline_text = "短文本片段"
    external_text = "x" * (8 * 1024 + 1)
    segments = [
        SegmentDraft(
            chunk_id="inline_1",
            node_id="n1",
            parent_node_id=None,
            ancestor_node_ids=[],
            segment_level="fine",
            title_path=["节"],
            start=0,
            end=len(inline_text),
            text=inline_text,
        ),
        SegmentDraft(
            chunk_id="external_1",
            node_id="n2",
            parent_node_id=None,
            ancestor_node_ids=[],
            segment_level="fine",
            title_path=["节"],
            start=0,
            end=len(external_text),
            text=external_text,
        ),
    ]

    await write_segments(db_session, task_id, file_id, segments, text_dir)
    await db_session.commit()

    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.file_id == file_id)
        )
    ).scalars().all()
    by_id = {c.chunk_id: c for c in chunks}

    assert load_chunk_text(by_id["inline_1"]) == inline_text
    assert load_chunk_text(by_id["external_1"]) == external_text
    assert by_id["inline_1"].text_inline == inline_text
    assert by_id["external_1"].text_path is not None
    assert Path(by_id["external_1"].text_path).is_file()
