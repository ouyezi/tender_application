import pytest_asyncio
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import get_db
from app.main import app
from app.models import Base
from app.services import parse_scheduler, scheduler


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    # NullPool avoids reusing pooled aiosqlite connections across requests: a
    # connection left checked out (e.g. by a lingering background task) at
    # ``engine.dispose()`` time can otherwise deadlock the event loop during
    # test teardown, since its worker thread never receives a close sentinel.
    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr("app.config.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.main.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.files.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.artifact.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.workspace.UPLOAD_DIR", upload_dir)
    (upload_dir).mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports").mkdir()
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "name", None) != "artifact-files"
    ]
    app.mount("/artifact-files", StaticFiles(directory=str(upload_dir)), name="artifact-files")
    monkeypatch.setattr("app.services.report.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("app.config.MOCK_ITEM_DELAY_SECONDS", 0.05)
    monkeypatch.setattr("app.services.scheduler.MOCK_ITEM_DELAY_SECONDS", 0.05)
    monkeypatch.setattr("app.config.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
    monkeypatch.setattr("app.services.scheduler.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
    await scheduler.reset_for_tests()
    await parse_scheduler.reset_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await scheduler.reset_for_tests()
    await parse_scheduler.reset_for_tests()
    app.dependency_overrides.clear()
    await engine.dispose()
