import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import DEFAULT_KNOWLEDGE_TAGS, init_db_on_connection
from app.models import DiagnosisResult, DiagnosisTask, KnowledgeTag


@pytest.mark.asyncio
async def test_migrate_adds_tender_file_id_to_legacy_tasks_table(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Simulate a pre-workspace schema: diagnosis_tasks without tender_file_id.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE diagnosis_tasks (
                    id VARCHAR(32) NOT NULL PRIMARY KEY,
                    tender_filename VARCHAR(512) NOT NULL,
                    tender_path VARCHAR(1024) NOT NULL,
                    bid_filename VARCHAR(512) NOT NULL,
                    bid_path VARCHAR(1024) NOT NULL,
                    background TEXT,
                    requirements TEXT,
                    status VARCHAR(32) NOT NULL,
                    progress_done INTEGER,
                    progress_total INTEGER,
                    config_snapshot TEXT NOT NULL,
                    report_md_path VARCHAR(1024),
                    report_docx_path VARCHAR(1024),
                    error_message TEXT,
                    created_at DATETIME,
                    updated_at DATETIME,
                    finished_at DATETIME
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE diagnosis_results (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    task_id VARCHAR(32) NOT NULL,
                    config_id INTEGER,
                    content_title VARCHAR(200) NOT NULL,
                    description TEXT,
                    result VARCHAR(64) NOT NULL,
                    evidence TEXT,
                    suggestion TEXT,
                    sort_order INTEGER,
                    created_at DATETIME,
                    FOREIGN KEY(task_id) REFERENCES diagnosis_tasks (id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO diagnosis_results (
                    task_id, content_title, description, result, evidence,
                    suggestion, sort_order
                ) VALUES (
                    'T-LEGACY-001', '旧检查项', '', '通过', '', '', 0
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO diagnosis_tasks (
                    id, tender_filename, tender_path, bid_filename, bid_path,
                    background, requirements, status, progress_done, progress_total,
                    config_snapshot
                ) VALUES (
                    'T-LEGACY-001', 't.docx', '/u/t.docx', 'b.docx', '/u/b.docx',
                    '', '', 'completed', 0, 0, '[]'
                )
                """
            )
        )

    monkeypatch.setattr("app.db.engine", engine)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    async with session_factory() as session:
        task = await session.get(DiagnosisTask, "T-LEGACY-001")
        assert task is not None
        assert task.tender_file_id is None
        assert task.bid_file_id is None
        assert task.current_checklist_generation_id is None
        assert task.status == "completed"
        result = await session.get(DiagnosisResult, 1)
        assert result is not None
        assert result.content_title == "旧检查项"
        assert result.checklist_item_id is None
        assert result.compliance_status is None
        assert result.consequence_tags == "[]"

    # New workspace and checklist tables should exist.
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ("
                    "'workspace_files', 'parse_jobs', 'checklist_generations', "
                    "'checklist_categories', 'checklist_items'"
                    ")"
                )
            )
        ).fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "workspace_files",
            "parse_jobs",
            "checklist_generations",
            "checklist_categories",
            "checklist_items",
        }

        checklist_item_columns = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info('checklist_items')"))
            ).fetchall()
        }
        assert "diagnosis_mode" in checklist_item_columns

        result_columns = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info('diagnosis_results')"))
            ).fetchall()
        }
        assert {
            "checklist_item_id",
            "compliance_status",
            "consequence_tags",
        }.issubset(result_columns)

        index_names = [
            row[1]
            for row in (
                await conn.execute(text("PRAGMA index_list('diagnosis_results')"))
            ).fetchall()
        ]
        indexed_columns = {
            tuple(
                row[2]
                for row in (
                    await conn.execute(text(f"PRAGMA index_info('{index_name}')"))
                ).fetchall()
            )
            for index_name in index_names
        }
        assert ("checklist_item_id",) in indexed_columns

    await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_retrieval_tables_exist(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.engine", engine)
    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ("
                    "'knowledge_chunks', 'knowledge_tags', 'wiki_pages', 'index_jobs'"
                    ")"
                )
            )
        ).fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "knowledge_chunks",
            "knowledge_tags",
            "wiki_pages",
            "index_jobs",
        }
    async with session_factory() as session:
        tags = (await session.scalars(select(KnowledgeTag))).all()
        tag_names = {t.name for t in tags}
        assert tag_names == {name for name, _, _ in DEFAULT_KNOWLEDGE_TAGS}
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_adds_checklist_content_source_columns(tmp_path, monkeypatch):
    db_path = tmp_path / "checklist_content.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE checklist_items (
                    id VARCHAR(64) NOT NULL PRIMARY KEY,
                    generation_id INTEGER NOT NULL,
                    category_id VARCHAR(64) NOT NULL,
                    title VARCHAR(200) NOT NULL,
                    requirement TEXT NOT NULL,
                    technique TEXT NOT NULL,
                    importance VARCHAR(16) NOT NULL,
                    source_references TEXT NOT NULL,
                    retrieval_hints TEXT NOT NULL,
                    expected_evidence TEXT NOT NULL,
                    compliance_rules TEXT NOT NULL,
                    consequence_rules TEXT NOT NULL,
                    admin_config_refs TEXT NOT NULL DEFAULT '[]',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME
                )
                """
            )
        )

    monkeypatch.setattr("app.db.engine", engine)

    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)

    async with engine.begin() as conn:
        columns = {
            row[1]
            for row in (
                await conn.execute(text("PRAGMA table_info('checklist_items')"))
            ).fetchall()
        }
        assert "content_source" in columns
        assert "content_target" in columns
        assert "diagnosis_mode" in columns

    await engine.dispose()
