from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, KnowledgeChunk, WikiPage
from app.services.retrieval.wiki_agent_os import (
    RETRIEVAL_WIKI_WRITER_APP_NAME,
    AgentOSWikiBuilder,
    WikiBuilderResponseError,
)


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    db_path = tmp_path / "wiki_agent_os_test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    monkeypatch.setattr("app.db.SessionLocal", session_factory)

    async with session_factory() as session:
        yield session

    await engine.dispose()


async def _seed_refund_chunk(session, task_id: str = "T-WIKI-1") -> str:
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
    chunk_id = "c-refund"
    session.add(
        KnowledgeChunk(
            task_id=task_id,
            file_id="f1",
            chunk_id=chunk_id,
            node_id="n1",
            segment_level="fine",
            index_status="ready",
            title="退款条款",
            summary="七天内可退",
            description="退款相关描述",
            tags=json.dumps(
                [{"name": "退款政策", "confidence": 0.9}],
                ensure_ascii=False,
            ),
        )
    )
    await session.commit()
    return task_id


@pytest.mark.asyncio
async def test_wiki_builder_groups_then_writes_copy(db_session):
    task_id = await _seed_refund_chunk(db_session)

    async def fake_invoke(app_name, input_data):
        assert app_name == RETRIEVAL_WIKI_WRITER_APP_NAME
        assert input_data["task_id"] == task_id
        pages = json.loads(input_data["pages_json"])
        assert len(pages) == 1
        assert pages[0]["tag_name"] == "退款政策"
        assert pages[0]["member_chunk_ids"] == ["c-refund"]
        assert pages[0]["member_summaries"] == [
            {
                "chunk_id": "c-refund",
                "title": "退款条款",
                "summary": "七天内可退",
            }
        ]
        return {
            "pages_json": json.dumps(
                [
                    {
                        "tag_name": "退款政策",
                        "title": "退款政策Wiki",
                        "summary": "汇总退款相关条款",
                        "description": "用于检索退款主题",
                    }
                ],
                ensure_ascii=False,
            )
        }

    builder = AgentOSWikiBuilder(invoke_app=fake_invoke)
    await builder.build_for_task(db_session, task_id)
    await db_session.commit()

    pages = (
        await db_session.execute(select(WikiPage).where(WikiPage.task_id == task_id))
    ).scalars().all()
    assert len(pages) == 1
    page = pages[0]
    assert page.title == "退款政策Wiki"
    assert page.summary == "汇总退款相关条款"
    assert page.description == "用于检索退款主题"
    assert json.loads(page.member_chunk_ids) == ["c-refund"]
    assert json.loads(page.tags) == ["退款政策"]


@pytest.mark.asyncio
async def test_wiki_builder_missing_page_copy_raises(db_session):
    task_id = await _seed_refund_chunk(db_session, task_id="T-WIKI-2")

    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"pages_json": json.dumps([])}

    builder = AgentOSWikiBuilder(invoke_app=fake_invoke)
    with pytest.raises(WikiBuilderResponseError):
        await builder.build_for_task(db_session, task_id)
    await db_session.rollback()

    pages = (
        await db_session.execute(select(WikiPage).where(WikiPage.task_id == task_id))
    ).scalars().all()
    assert pages == []
