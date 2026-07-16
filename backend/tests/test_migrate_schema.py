import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import init_db_on_connection
from app.models import DiagnosisTask


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
        assert task.status == "completed"

    # New workspace tables should exist.
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('workspace_files', 'parse_jobs')"
                )
            )
        ).fetchall()
        names = {r[0] for r in rows}
        assert names == {"workspace_files", "parse_jobs"}

    await engine.dispose()
