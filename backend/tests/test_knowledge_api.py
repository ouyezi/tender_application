from __future__ import annotations

import json

import pytest
import pytest_asyncio
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import get_db, init_db_on_connection
from app.main import app
from app.models import DiagnosisTask, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from tests.stubs.retrieval_ai import apply_retrieval_ai_stubs


@pytest_asyncio.fixture
async def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge_api.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("app.config.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.main.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.artifact.UPLOAD_DIR", upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    apply_retrieval_ai_stubs(monkeypatch)

    app.router.routes = [
        route
        for route in app.router.routes
        if getattr(route, "name", None) != "artifact-files"
    ]
    app.mount(
        "/artifact-files", StaticFiles(directory=str(upload_dir)), name="artifact-files"
    )

    await index_scheduler.reset_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await index_scheduler.reset_for_tests()
    app.dependency_overrides.clear()
    await engine.dispose()


async def _write_parsed_file(
    session_factory,
    *,
    task_id: str,
    file_id: str,
    label: str,
    md_text: str,
) -> WorkspaceFile:
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

    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="tender.docx",
                tender_path="/tmp/tender.docx",
                bid_filename="bid.docx",
                bid_path="/tmp/bid.docx",
                config_snapshot="[]",
            )
        )
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
        session.add(wf)
        await session.commit()
        await session.refresh(wf)
        return wf


@pytest_asyncio.fixture
async def indexed_task_id(api_client):
    # Reuse the same SessionLocal patched by api_client
    from app.db import SessionLocal

    task_id = "T-API-KNOW"
    file_id = "fapi001"
    md_text = (
        "# 售后服务\n\n"
        "本公司提供完整售后服务与质保支持。\n\n"
        "## 退款政策\n\n"
        "本商品支持七天无理由退货，购买后7天内可无理由申请退款。\n\n"
        "## 质保说明\n\n"
        "产品质保期为一年，质保期内免费维修。\n"
    )
    wf = await _write_parsed_file(
        SessionLocal,
        task_id=task_id,
        file_id=file_id,
        label="售后政策文件",
        md_text=md_text,
    )
    await index_scheduler.enqueue(wf.task_id, wf.id)
    await index_scheduler.drain_once_for_tests()
    return task_id


@pytest.mark.asyncio
async def test_api_chunks_and_debug_retrieve(api_client, indexed_task_id):
    r = await api_client.get(f"/api/workspaces/{indexed_task_id}/knowledge/chunks")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body

    r = await api_client.get(f"/api/workspaces/{indexed_task_id}/knowledge/index-status")
    assert r.status_code == 200
    assert r.json()["index_status"] in ("ready", "partial", "unavailable")

    r = await api_client.post(
        f"/api/workspaces/{indexed_task_id}/knowledge/debug/retrieve",
        json={
            "content_source": "precise_search",
            "content_target": {"query": "七天无理由"},
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "precise_search"
    assert data["trace"]["rewrite"]

    r = await api_client.post(
        f"/api/workspaces/{indexed_task_id}/knowledge/debug/retrieve",
        json={
            "content_source": "collection",
            "content_target": {"target_tags": ["不是合法标签xyz"]},
        },
    )
    assert r.status_code == 400
    detail = r.json().get("detail")
    assert "allowed" in str(detail).lower() or isinstance(detail, dict)


@pytest.mark.asyncio
async def test_api_task_not_found(api_client):
    r = await api_client.get("/api/workspaces/T-missing/knowledge/chunks")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_chunk_cross_task_404(api_client, indexed_task_id):
    from app.db import SessionLocal

    r = await api_client.get(
        f"/api/workspaces/{indexed_task_id}/knowledge/chunks"
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items
    chunk_id = items[0]["chunk_id"]

    other_id = "T-API-OTHER"
    async with SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id=other_id,
                tender_filename="tender.docx",
                tender_path="/tmp/tender.docx",
                bid_filename="bid.docx",
                bid_path="/tmp/bid.docx",
                config_snapshot="[]",
            )
        )
        await session.commit()

    r = await api_client.get(
        f"/api/workspaces/{other_id}/knowledge/chunks/{chunk_id}"
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Chunk not found"
