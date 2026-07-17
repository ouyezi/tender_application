from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import KnowledgeChunk
from app.services.retrieval.fts import rebuild_fts_for_file, search_fts


@pytest_asyncio.fixture
async def indexed_chunks_session(tmp_path):
    db_path = tmp_path / "fts_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    async with session_factory() as session:
        session.add(
            KnowledgeChunk(
                task_id="T-1",
                file_id="f-1",
                chunk_id="chunk-auth-1",
                node_id="n1",
                segment_level="fine",
                title="法人授权证书",
                summary="授权材料摘要",
                description="授权材料说明",
                text_inline="投标人须提交完整的法人授权证书及身份证明。",
                index_status="ready",
            )
        )
        await session.commit()
        await rebuild_fts_for_file(session, "T-1", "f-1")
        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_fts_finds_chinese_keywords(indexed_chunks_session):
    hits = await search_fts(
        indexed_chunks_session, task_id="T-1", query="授权证书", limit=10
    )
    assert hits
    assert any("授权" in h["title"] or "授权" in h["snippet"] for h in hits)


@pytest.mark.asyncio
async def test_fts_reindex_replaces_rows(indexed_chunks_session):
    await indexed_chunks_session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.chunk_id == "chunk-auth-1")
    )
    indexed_chunks_session.add(
        KnowledgeChunk(
            task_id="T-1",
            file_id="f-1",
            chunk_id="chunk-auth-2",
            node_id="n2",
            segment_level="fine",
            title="营业执照",
            summary="执照摘要",
            description="执照说明",
            text_inline="有效营业执照副本。",
            index_status="ready",
        )
    )
    await indexed_chunks_session.commit()
    await rebuild_fts_for_file(indexed_chunks_session, "T-1", "f-1")
    await indexed_chunks_session.commit()

    auth_hits = await search_fts(
        indexed_chunks_session, task_id="T-1", query="授权证书", limit=10
    )
    license_hits = await search_fts(
        indexed_chunks_session, task_id="T-1", query="营业执照", limit=10
    )
    assert not auth_hits
    assert license_hits
    assert license_hits[0]["chunk_id"] == "chunk-auth-2"
