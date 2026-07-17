from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.engine.retrieval_workspace import WorkspaceRetrievalProvider
from app.models import DiagnosisTask, KnowledgeTag, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree

FIXTURES = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "retrieval_modes.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    async with session_factory() as seed_session:
        # 技术方案 is not in DEFAULT_KNOWLEDGE_TAGS; 授权证书 is seeded by init_db.
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


@pytest.fixture
def provider():
    return WorkspaceRetrievalProvider()


async def _write_parsed_file(
    db_session,
    *,
    task_id: str,
    file_id: str,
    label: str,
    md_text: str,
    md_fixture: Path | None = None,
) -> WorkspaceFile:
    if md_fixture is not None:
        md_text = md_fixture.read_text(encoding="utf-8")

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
async def indexed_task_with_tender(db_session):
    task_id = "T-RETR-FULL"
    tender_id = "ftender001"
    wf = await _write_parsed_file(
        db_session,
        task_id=task_id,
        file_id=tender_id,
        label="招标文件",
        md_text="# 招标文件\n\n本招标文件全文内容。",
    )
    task = (
        await db_session.execute(
            select(DiagnosisTask).where(DiagnosisTask.id == task_id)
        )
    ).scalar_one()
    task.tender_file_id = tender_id
    await db_session.commit()
    await _index_file(wf)
    return task


@pytest_asyncio.fixture
async def indexed_task_with_tagged_chunks(db_session):
    task_id = "T-RETR-COLL"
    file_id = "fcoll001"
    md_text = "# 资质材料\n\n## 授权证书\n\n兹授权某某公司作为投标授权代表。\n"
    wf = await _write_parsed_file(
        db_session,
        task_id=task_id,
        file_id=file_id,
        label="投标文件",
        md_text=md_text,
    )
    await _index_file(wf)
    return task_id


@pytest_asyncio.fixture
async def indexed_bid_task(db_session):
    task_id = "T-RETR-LARGE"
    bid_id = "fbid001"
    wf = await _write_parsed_file(
        db_session,
        task_id=task_id,
        file_id=bid_id,
        label="投标文件",
        md_text="",
        md_fixture=FIXTURES / "retrieval_sample.md",
    )
    task = (
        await db_session.execute(
            select(DiagnosisTask).where(DiagnosisTask.id == task_id)
        )
    ).scalar_one()
    task.bid_file_id = bid_id
    await db_session.commit()
    await _index_file(wf)
    return task_id


@pytest.mark.asyncio
async def test_full_document_returns_markdown(provider, indexed_task_with_tender):
    result = await provider.retrieve(
        task_id=indexed_task_with_tender.id,
        content_source="full_document",
        content_target={"file_role": "tender"},
    )
    assert result.mode == "full_document"
    assert result.error is None
    assert len(result.items) == 1
    assert "招标文件" in result.items[0].text
    assert len(result.items[0].text) > 0


@pytest.mark.asyncio
async def test_collection_filters_by_tag(provider, indexed_task_with_tagged_chunks):
    result = await provider.retrieve(
        task_id=indexed_task_with_tagged_chunks,
        content_source="collection",
        content_target={"target_tags": ["授权证书"]},
    )
    assert result.mode == "collection"
    assert result.error is None
    assert result.items
    assert all(
        any(t["name"] == "授权证书" for t in hit.tags) for hit in result.items
    )


@pytest.mark.asyncio
async def test_large_segments_returns_large_only(provider, indexed_bid_task):
    result = await provider.retrieve(
        task_id=indexed_bid_task,
        content_source="large_segments",
        content_target={"file_role": "bid"},
    )
    assert result.mode == "large_segments"
    assert result.error is None
    assert result.items
    assert all(h.segment_level == "large" for h in result.items)
    assert all(h.child_chunk_ids is not None for h in result.items)


@pytest.mark.asyncio
async def test_missing_content_source_is_config_error(provider):
    result = await provider.retrieve(
        task_id="T-x",
        content_source="",
        content_target={},
    )
    assert result.error


@pytest.mark.asyncio
async def test_precise_search_not_implemented(provider, indexed_bid_task):
    result = await provider.retrieve(
        task_id=indexed_bid_task,
        content_source="precise_search",
        content_target={"query": "退款政策"},
    )
    assert result.mode == "precise_search"
    assert result.error == "not_implemented"


@pytest.mark.asyncio
async def test_retrieve_for_category_dedupes_by_chunk_id(
    provider, indexed_task_with_tagged_chunks
):
    items = [
        {
            "content_source": "collection",
            "content_target": {"target_tags": ["授权证书"]},
            "title": "授权1",
        },
        {
            "content_source": "collection",
            "content_target": {"target_tags": ["授权证书"]},
            "title": "授权2",
        },
    ]
    chunks = await provider.retrieve_for_category(
        task_id=indexed_task_with_tagged_chunks,
        category={"name": "资质"},
        items=items,
    )
    chunk_ids = [c.chunk_id for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert chunks
    assert all(c.location for c in chunks)
