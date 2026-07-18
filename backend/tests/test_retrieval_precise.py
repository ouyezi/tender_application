from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.engine.retrieval_workspace import WorkspaceRetrievalProvider
from app.models import DiagnosisTask, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from tests.stubs.retrieval_ai import apply_retrieval_ai_stubs

FIXTURES = Path(__file__).parent / "fixtures"


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "precise_search.db"
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
    task_id = "T-RETR-PREC"
    file_id = "fprec001"
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
async def test_precise_search_merges_channels_and_reranks(
    provider, indexed_semantic_task
):
    result = await provider.retrieve(
        task_id=indexed_semantic_task,
        content_source="precise_search",
        content_target={"query": "是否支持七天无理由退货"},
        item_hints={"retrieval_hints": ["售后", "退款政策"]},
    )
    assert result.mode == "precise_search"
    assert result.error is None
    assert result.items
    assert result.items[0].title
    assert any(
        "退款" in hit.text or "无理由" in hit.text for hit in result.items
    )
    assert all(hit.segment_level == "large" for hit in result.items)


@pytest.mark.asyncio
async def test_precise_search_fails_when_reranker_raises(
    provider, monkeypatch, indexed_semantic_task
):
    async def boom(*a, **k):
        raise RuntimeError("rerank down")

    monkeypatch.setattr(provider, "_ai_rerank", boom)
    with pytest.raises(RuntimeError, match="rerank down"):
        await provider.retrieve(
            task_id=indexed_semantic_task,
            content_source="precise_search",
            content_target={"query": "退款"},
        )


@pytest.mark.asyncio
async def test_precise_search_fails_when_rewriter_raises(
    provider, monkeypatch, indexed_semantic_task
):
    class Boom:
        async def rewrite(self, query, hints=None):
            raise RuntimeError("rewrite down")

    monkeypatch.setattr(
        "app.services.retrieval.provider.get_query_rewriter",
        lambda: Boom(),
    )
    with pytest.raises(RuntimeError, match="rewrite down"):
        await provider.retrieve(
            task_id=indexed_semantic_task,
            content_source="precise_search",
            content_target={"query": "退款"},
        )


@pytest.mark.asyncio
async def test_precise_search_falls_back_to_hints_when_query_empty(
    provider, monkeypatch, indexed_semantic_task
):
    captured = {}

    class Capture:
        async def rewrite(self, query, hints=None):
            captured["query"] = query
            captured["hints"] = list(hints or [])
            return {
                "vector_query": query,
                "keywords": [query],
                "wiki_query": query,
            }

    monkeypatch.setattr(
        "app.services.retrieval.provider.get_query_rewriter",
        lambda: Capture(),
    )
    result = await provider.retrieve(
        task_id=indexed_semantic_task,
        content_source="precise_search",
        content_target={},
        item_hints={"retrieval_hints": ["七天无理由退货"]},
    )
    assert captured["query"] == "七天无理由退货"
    assert result.mode == "precise_search"
    assert result.error is None
