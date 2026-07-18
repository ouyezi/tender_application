from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from tests.stubs.retrieval_ai import apply_retrieval_ai_stubs


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "browse.db"
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


async def _write_parsed_file(
    db_session,
    *,
    task_id: str,
    file_id: str,
    label: str,
    md_text: str,
) -> WorkspaceFile:
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
    md_path.parent.mkdir(parents=True, exist_ok=True)
    tree_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")
    tree_path.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
    chunks_path.write_text(json.dumps(fine_chunks, ensure_ascii=False), encoding="utf-8")

    wf = WorkspaceFile(
        id=file_id,
        task_id=task_id,
        label=label,
        original_filename=f"{file_id}.md",
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
    await db_session.refresh(wf)
    return wf


async def _index_file(wf: WorkspaceFile) -> None:
    await index_scheduler.enqueue(wf.task_id, wf.id)
    await index_scheduler.drain_once_for_tests()


@pytest_asyncio.fixture
async def indexed_semantic_task(db_session):
    task_id = "T-BRW-PREC"
    file_id = "fbrw001"
    md_text = (
        "# 售后服务\n\n"
        "本公司提供完整售后服务与质保支持。\n\n"
        "## 退款政策\n\n"
        "本商品支持七天无理由退货，购买后7天内可无理由申请退款。\n\n"
        "## 质保说明\n\n"
        "产品质保期为一年，质保期内免费维修。\n"
    )
    wf = await _write_parsed_file(
        db_session,
        task_id=task_id,
        file_id=file_id,
        label="售后政策文件",
        md_text=md_text,
    )
    await _index_file(wf)
    return task_id


@pytest.mark.asyncio
async def test_list_chunks_filters_segment_level(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_chunks

    page = await list_chunks(
        db_session,
        task_id=indexed_semantic_task,
        segment_level="fine",
        page=1,
        page_size=50,
    )
    assert page["total"] >= 1
    assert all(c["segment_level"] == "fine" for c in page["items"])
    assert "title" in page["items"][0]
    assert "search_degraded" in page


@pytest.mark.asyncio
async def test_get_chunk_detail_includes_text(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_chunks, get_chunk

    page = await list_chunks(db_session, task_id=indexed_semantic_task, page=1, page_size=5)
    chunk_id = page["items"][0]["chunk_id"]
    detail = await get_chunk(db_session, task_id=indexed_semantic_task, chunk_id=chunk_id)
    assert detail["chunk_id"] == chunk_id
    assert "text" in detail
    assert "tags" in detail


@pytest.mark.asyncio
async def test_list_chunks_q_matches_title_or_body(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_chunks

    page = await list_chunks(
        db_session, task_id=indexed_semantic_task, q="无理由", page=1, page_size=20
    )
    assert page["total"] >= 1
