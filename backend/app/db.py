from collections.abc import AsyncGenerator

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DATABASE_URL
from app.models import Base, DiagnosisTask

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def recover_interrupted_tasks() -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(DiagnosisTask)
            .where(DiagnosisTask.status.in_(["running", "paused"]))
            .values(status="stopped")
        )
        await session.commit()
