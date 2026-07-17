import pytest_asyncio
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import get_db
from app.main import app
from app.models import Base
from app.services import index_scheduler, parse_scheduler, scheduler


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
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    monkeypatch.setattr("app.services.artifact.REPORT_DIR", report_dir)
    app.router.routes = [
        route for route in app.router.routes if getattr(route, "name", None) != "artifact-files"
    ]
    app.mount("/artifact-files", StaticFiles(directory=str(upload_dir)), name="artifact-files")
    monkeypatch.setattr("app.services.report.REPORT_DIR", report_dir)
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", report_dir)
    monkeypatch.setattr("app.config.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
    monkeypatch.setattr("app.services.scheduler.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
    monkeypatch.setattr("app.config.MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS", 0.15)
    monkeypatch.setattr("app.services.scheduler.MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS", 0.15)
    monkeypatch.setattr("app.config.CHECKLIST_PARSE_POLL_SECONDS", 0.01)
    monkeypatch.setattr("app.config.RETRIEVAL_PROVIDER", "mock")

    async def _fake_parse_pipeline(file_id: str, task_id: str, stored_path: str):
        from app.services import artifact

        del stored_path
        root = artifact.ensure_artifact_dirs(task_id)
        md_path = root / "markdown" / f"{file_id}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(
            "# 资格要求\n"
            "必须提交营业执照。\n"
            "投标人应具备市政公用工程施工总承包一级资质。\n\n"
            "# 业绩与商务得分\n"
            "须提供近三年的业绩证明材料。\n"
            "商务得分条款须逐条响应。\n\n"
            "# 技术响应\n"
            "技术方案须响应全部关键参数。\n",
            encoding="utf-8",
        )
        return {
            "status": "succeeded",
            "md_path": str(md_path),
            "tree_path": None,
            "chunks_path": None,
            "error": None,
            "warnings": [],
        }

    monkeypatch.setattr(
        "app.services.parse_scheduler.run_parse_pipeline",
        _fake_parse_pipeline,
    )
    await scheduler.reset_for_tests()
    await parse_scheduler.reset_for_tests()
    await index_scheduler.reset_for_tests()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    await scheduler.reset_for_tests()
    await parse_scheduler.reset_for_tests()
    await index_scheduler.reset_for_tests()
    app.dependency_overrides.clear()
    await engine.dispose()
