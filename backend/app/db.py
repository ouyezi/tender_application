from collections.abc import AsyncGenerator

from sqlalchemy import inspect, text, update
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DATABASE_URL
from app.models import Base, DiagnosisTask, ParseJob, WorkspaceFile, utcnow

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _migrate_sqlite_columns(sync_conn) -> None:
    """Add missing nullable columns on existing SQLite tables.

    ``create_all`` only creates missing tables; it does not ALTER existing ones.
    Demo DBs created before workspace fields need this lightweight upgrade.
    """
    inspector = inspect(sync_conn)
    dialect = sqlite.dialect()
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if not column.nullable and column.server_default is None:
                # Avoid ADD COLUMN NOT NULL without a default on populated tables.
                continue
            col_type = column.type.compile(dialect=dialect)
            sync_conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}"))


def init_db_on_connection(sync_conn) -> None:
    Base.metadata.create_all(sync_conn)
    _migrate_sqlite_columns(sync_conn)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def recover_interrupted_tasks() -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(DiagnosisTask)
            .where(
                DiagnosisTask.status.in_(
                    ["interpreting", "diagnosing", "running", "paused"]
                )
            )
            .values(status="stopped", updated_at=utcnow())
        )
        await session.commit()


async def recover_interrupted_parse_jobs() -> None:
    """Reset jobs/files left ``running`` by an unclean shutdown so the parse
    scheduler picks them back up on the next tick."""
    async with SessionLocal() as session:
        await session.execute(
            update(ParseJob)
            .where(ParseJob.status == "running")
            .values(status="queued", stage="convert")
        )
        await session.execute(
            update(WorkspaceFile)
            .where(WorkspaceFile.parse_status == "running")
            .values(parse_status="pending", updated_at=utcnow())
        )
        await session.commit()
