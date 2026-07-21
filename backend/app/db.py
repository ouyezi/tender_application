from collections.abc import AsyncGenerator
import json

from sqlalchemy import event, inspect, text, update
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateColumn, CreateIndex

from app.config import DATABASE_URL
from app.models import (
    Base,
    ChecklistGeneration,
    DiagnosisTask,
    ExecutionNode,
    IndexJob,
    KnowledgeChunk,
    KnowledgeTag,
    ParseJob,
    WikiPage,
    WorkspaceFile,
    utcnow,
)
from app.services.retrieval.fts import create_fts_table_sql

# SQLite allows only one writer; background schedulers + API requests contend on
# the same file. WAL + busy_timeout + NullPool reduce "database is locked" errors.
SQLITE_BUSY_TIMEOUT_MS = 30_000
_IS_SQLITE = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {"echo": False}
if _IS_SQLITE:
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs["connect_args"] = {"timeout": SQLITE_BUSY_TIMEOUT_MS / 1000}

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


if _IS_SQLITE:
    event.listens_for(engine.sync_engine, "connect")(_configure_sqlite_connection)

DEFAULT_KNOWLEDGE_TAGS = [
    ("授权证书", ["授权书", "授权函"], "投标授权类材料"),
    ("资质证明", ["资质文件", "资质证书"], "企业/人员资质类材料"),
    ("营业执照", [], "营业执照"),
    ("售后政策", ["售后服务", "质保"], "售后与质保条款"),
    ("退款政策", ["退款", "七天无理由", "7天无理由"], "退款与无理由退货"),
]


def _migrate_sqlite_columns(sync_conn) -> None:
    """Add missing nullable columns on existing SQLite tables.

    ``create_all`` only creates missing tables; it does not ALTER existing ones.
    Demo DBs created before workspace fields need this lightweight upgrade.
    """
    inspector = inspect(sync_conn)
    dialect = sqlite.dialect()
    for table in Base.metadata.tables.values():
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if not column.nullable and column.server_default is None:
                # Avoid ADD COLUMN NOT NULL without a default on populated tables.
                continue
            if column.server_default is not None:
                column_ddl = CreateColumn(column).compile(dialect=dialect)
            else:
                column_ddl = f"{column.name} {column.type.compile(dialect=dialect)}"
            sync_conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column_ddl}"))


def _migrate_sqlite_indexes(sync_conn) -> None:
    """Create non-unique metadata indexes missing from existing SQLite tables."""
    inspector = inspect(sync_conn)
    for table in Base.metadata.tables.values():
        if not inspector.has_table(table.name):
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table.name)
        }
        for index in table.indexes:
            index_columns = {column.name for column in index.columns}
            if index.unique or not index_columns.issubset(existing_columns):
                continue
            sync_conn.execute(CreateIndex(index, if_not_exists=True))


def _seed_knowledge_tags_sync(sync_conn) -> None:
    existing = {
        row[0]
        for row in sync_conn.execute(text("SELECT name FROM knowledge_tags")).fetchall()
    }
    for name, aliases, description in DEFAULT_KNOWLEDGE_TAGS:
        if name in existing:
            continue
        sync_conn.execute(
            text(
                "INSERT INTO knowledge_tags (name, aliases, description, enabled) "
                "VALUES (:name, :aliases, :description, 1)"
            ),
            {"name": name, "aliases": json.dumps(aliases), "description": description},
        )


def _create_fts_table(sync_conn) -> None:
    sync_conn.execute(text(create_fts_table_sql()))


def init_db_on_connection(sync_conn) -> None:
    Base.metadata.create_all(sync_conn)
    _migrate_sqlite_columns(sync_conn)
    _migrate_sqlite_indexes(sync_conn)
    _create_fts_table(sync_conn)
    _seed_knowledge_tags_sync(sync_conn)


async def seed_knowledge_tags() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_seed_knowledge_tags_sync)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def recover_interrupted_tasks() -> None:
    async with SessionLocal() as session:
        now = utcnow()
        await session.execute(
            update(DiagnosisTask)
            .where(
                DiagnosisTask.status.in_(
                    [
                        "interpreting",
                        "generating_checklist",
                        "indexing_bid",
                        "diagnosing",
                        "running",
                        "paused",
                    ]
                )
            )
            .values(status="stopped", updated_at=now)
        )
        await session.execute(
            update(ChecklistGeneration)
            .where(ChecklistGeneration.status == "generating")
            .values(
                status="failed",
                error_message="interrupted",
                finished_at=now,
            )
        )
        await session.execute(
            update(ExecutionNode)
            .where(ExecutionNode.status == "running")
            .values(status="interrupted")
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
