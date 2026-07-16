# 标书诊断 Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可本地运行的标书诊断 demo：FastAPI + SQLite 后端（Mock 诊断引擎、任务暂停/继续/停止）与 React 前端（任务卡片、详情报告、管理配置）。

**Architecture:** 轻量单体。FastAPI 提供 REST 与文件存取；进程内 asyncio 调度器按配置快照逐项调用 `DiagnosisEngine`（默认 `MockEngine`）；SQLite 存配置/任务/结果，文件落在 `uploads/` 与 `reports/`。React + Vite 单应用覆盖 `/`、`/tasks/:id`、`/admin/*`。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy 2.x、aiosqlite、python-docx、uvicorn；React 18、Vite、React Router、fetch API。

**Spec:** `docs/superpowers/specs/2026-07-16-tender-diagnosis-demo-design.md`

---

## File Structure

```text
tender_application/
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 # FastAPI app, CORS, lifespan recovery
│   │   ├── config.py               # paths, max upload size, DB URL
│   │   ├── db.py                   # engine, session, init_db
│   │   ├── models.py               # SQLAlchemy models
│   │   ├── schemas.py              # Pydantic schemas
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── configs.py          # config CRUD
│   │   │   └── tasks.py            # tasks + controls + downloads
│   │   ├── engine/
│   │   │   ├── __init__.py
│   │   │   ├── base.py             # DiagnosisEngine protocol + result type
│   │   │   └── mock.py             # MockEngine
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── files.py            # save/validate uploads
│   │   │   ├── scheduler.py        # asyncio runner, pause/resume/stop
│   │   │   └── report.py           # md + docx generation
│   │   └── seed.py                 # optional demo configs
│   └── tests/
│       ├── conftest.py
│       ├── test_configs.py
│       ├── test_tasks.py
│       ├── test_engine.py
│       ├── test_scheduler.py
│       └── test_report.py
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── api.js
│       ├── pages/
│       │   ├── TaskListPage.jsx
│       │   ├── TaskDetailPage.jsx
│       │   ├── admin/
│       │   │   ├── AdminLayout.jsx
│       │   │   ├── ConfigsPage.jsx
│       │   │   └── AdminTasksPage.jsx
│       └── components/
│           ├── TaskCard.jsx
│           ├── CreateTaskModal.jsx
│           ├── ResultTable.jsx
│           └── MarkdownPreview.jsx
├── uploads/                        # gitignored
├── reports/                        # gitignored
└── README.md
```

---

### Task 1: Backend scaffold & dependencies

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/app/__init__.py`
- Create: `backend/app/config.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/conftest.py`

- [ ] **Step 1: Create virtualenv and install deps**

```bash
cd /Users/tongqianni/xlab/tender_application
python3 -m venv .venv
.venv/bin/pip install fastapi "uvicorn[standard]" sqlalchemy aiosqlite python-multipart python-docx pydantic pytest httpx
.venv/bin/pip freeze > backend/requirements.txt
```

Expected: `backend/requirements.txt` contains the packages above.

- [ ] **Step 2: Write `backend/app/config.py`**

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT
UPLOAD_DIR = DATA_DIR / "uploads"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "tender_diagnosis.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MOCK_ITEM_DELAY_SECONDS = 0.8
```

- [ ] **Step 3: Write minimal FastAPI app**

`backend/app/__init__.py` — empty.

`backend/app/main.py`:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import REPORT_DIR, UPLOAD_DIR

app = FastAPI(title="Tender Diagnosis Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/api/health")
async def health():
    return {"ok": True}
```

- [ ] **Step 4: Write test conftest stub and health check**

`backend/tests/conftest.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

Create `backend/tests/test_health.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
```

Install pytest-asyncio if needed: `.venv/bin/pip install pytest-asyncio` and add to requirements.

Add `backend/pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
pythonpath = .
```

- [ ] **Step 5: Run health test**

```bash
cd /Users/tongqianni/xlab/tender_application/backend
../.venv/bin/pytest tests/test_health.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/tongqianni/xlab/tender_application
git add backend .gitignore
git commit -m "chore: scaffold FastAPI backend and health check"
```

---

### Task 2: Database models & session

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Write models**

`backend/app/models.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DiagnosisConfig(Base):
    __tablename__ = "diagnosis_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    technique: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_mode: Mapped[str] = mapped_column(String(32), nullable=False)  # full_text | description
    content_scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DiagnosisTask(Base):
    __tablename__ = "diagnosis_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tender_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    tender_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    bid_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    bid_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    background: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    progress_done: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    config_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    report_md_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    report_docx_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list["DiagnosisResult"]] = relationship(back_populates="task", cascade="all, delete-orphan")


class DiagnosisResult(Base):
    __tablename__ = "diagnosis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("diagnosis_tasks.id"), nullable=False, index=True)
    config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped["DiagnosisTask"] = relationship(back_populates="results")
```

- [ ] **Step 2: Write db helpers**

`backend/app/db.py`:

```python
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
```

- [ ] **Step 3: Hook lifespan in main.py**

Replace `@app.on_event("startup")` with lifespan:

```python
from contextlib import asynccontextmanager

from app.db import init_db, recover_interrupted_tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    await init_db()
    await recover_interrupted_tasks()
    yield


app = FastAPI(title="Tender Diagnosis Demo", lifespan=lifespan)
# keep CORS middleware as before
```

- [ ] **Step 4: Update conftest to use temp DB**

```python
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import get_db
from app.main import app
from app.models import Base


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.services.files.UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr("app.services.report.REPORT_DIR", tmp_path / "reports")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "reports").mkdir()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()
```

Note: files/report modules created in later tasks; until then only monkeypatch after those modules exist. For Task 2, keep simpler conftest without file monkeypatch; extend in Task 4.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/models.py backend/app/main.py backend/tests/conftest.py
git commit -m "feat: add SQLite models and startup recovery"
```

---

### Task 3: Config CRUD API

**Files:**
- Create: `backend/app/schemas.py`
- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/configs.py`
- Create: `backend/tests/test_configs.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing tests**

`backend/tests/test_configs.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_create_and_list_configs(client):
    payload = {
        "title": "企业资质核验",
        "technique": "对照招标资格要求",
        "content_mode": "description",
        "content_scope": None,
        "content_text": "所有资质文件",
        "importance": "high",
    }
    r = await client.post("/api/configs", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "企业资质核验"
    assert body["id"] > 0

    r2 = await client.get("/api/configs")
    assert r2.status_code == 200
    assert len(r2.json()) == 1


@pytest.mark.asyncio
async def test_update_and_delete_config(client):
    r = await client.post(
        "/api/configs",
        json={
            "title": "目录",
            "technique": "检查目录",
            "content_mode": "full_text",
            "content_scope": "directory",
            "content_text": None,
            "importance": "medium",
        },
    )
    cid = r.json()["id"]
    r2 = await client.put(
        f"/api/configs/{cid}",
        json={
            "title": "目录完整性",
            "technique": "检查目录与正文",
            "content_mode": "full_text",
            "content_scope": "directory",
            "content_text": None,
            "importance": "high",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["title"] == "目录完整性"

    r3 = await client.delete(f"/api/configs/{cid}")
    assert r3.status_code == 204
    assert (await client.get("/api/configs")).json() == []
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/pytest tests/test_configs.py -v
```

Expected: FAIL (404 or route missing)

- [ ] **Step 3: Implement schemas + API**

Add to `backend/app/schemas.py`:

```python
from datetime import datetime

from pydantic import BaseModel, Field


class ConfigCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    technique: str = ""
    content_mode: str  # full_text | description
    content_scope: str | None = None
    content_text: str | None = None
    importance: str = "medium"


class ConfigUpdate(ConfigCreate):
    pass


class ConfigOut(ConfigCreate):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

`backend/app/api/configs.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import DiagnosisConfig
from app.schemas import ConfigCreate, ConfigOut, ConfigUpdate

router = APIRouter(prefix="/api/configs", tags=["configs"])


@router.get("", response_model=list[ConfigOut])
async def list_configs(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(DiagnosisConfig).order_by(DiagnosisConfig.id))).scalars().all()
    return rows


@router.post("", response_model=ConfigOut, status_code=201)
async def create_config(payload: ConfigCreate, db: AsyncSession = Depends(get_db)):
    if payload.content_mode not in ("full_text", "description"):
        raise HTTPException(400, "content_mode must be full_text or description")
    if payload.importance not in ("high", "medium", "low"):
        raise HTTPException(400, "invalid importance")
    row = DiagnosisConfig(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/{config_id}", response_model=ConfigOut)
async def update_config(config_id: int, payload: ConfigUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.get(DiagnosisConfig, config_id)
    if not row:
        raise HTTPException(404, "config not found")
    for k, v in payload.model_dump().items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/{config_id}", status_code=204)
async def delete_config(config_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(DiagnosisConfig, config_id)
    if not row:
        raise HTTPException(404, "config not found")
    await db.delete(row)
    await db.commit()
```

Register in `main.py`: `app.include_router(configs.router)` (import from `app.api.configs`).

Ensure conftest overrides `get_db` with temp DB (complete the Task 2 conftest properly before this step).

- [ ] **Step 4: Run tests — expect PASS**

```bash
../.venv/bin/pytest tests/test_configs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/api backend/tests/test_configs.py backend/app/main.py backend/tests/conftest.py
git commit -m "feat: add diagnosis config CRUD API"
```

---

### Task 4: File helpers + task create/list/detail API (without engine yet)

**Files:**
- Create: `backend/app/services/files.py`
- Create: `backend/app/api/tasks.py`
- Extend: `backend/app/schemas.py`
- Create: `backend/tests/test_tasks.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write file service**

`backend/app/services/__init__.py` — empty.

`backend/app/services/files.py`:

```python
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import ALLOWED_EXTENSIONS, MAX_UPLOAD_BYTES, UPLOAD_DIR


def validate_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"only {', '.join(sorted(ALLOWED_EXTENSIONS))} allowed")
    return ext


async def save_upload(file: UploadFile, task_id: str, kind: str) -> tuple[str, str]:
    if not file.filename:
        raise HTTPException(400, f"{kind} file required")
    ext = validate_extension(file.filename)
    dest_dir = UPLOAD_DIR / task_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{kind}{ext}"
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "file too large (max 50MB)")
            out.write(chunk)
    return file.filename, str(dest)
```

- [ ] **Step 2: Write failing task tests**

`backend/tests/test_tasks.py` (create with two tiny fake files; engine schedule can be no-op stub first):

```python
import io

import pytest


def _pdf_bytes():
    return b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_create_task_requires_files(client):
    r = await client.post("/api/tasks", data={"background": "x"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_create_and_get_task(client):
    # seed one config so snapshot non-empty
    await client.post(
        "/api/configs",
        json={
            "title": "资质",
            "technique": "查",
            "content_mode": "description",
            "content_text": "资质",
            "importance": "high",
        },
    )
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    }
    data = {"background": "市政项目", "requirements": "核资质"}
    r = await client.post("/api/tasks", data=data, files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] in ("running", "completed", "paused")
    assert body["tender_filename"] == "tender.pdf"
    assert body["progress_total"] == 1

    r2 = await client.get(f"/api/tasks/{body['id']}")
    assert r2.status_code == 200
    assert r2.json()["background"] == "市政项目"
```

- [ ] **Step 3: Implement task schemas + routes (schedule stub)**

Add task schemas (`TaskOut`, `ResultOut`, list variants) in `schemas.py`.

`tasks.py` create flow:

1. Generate `task_id = f"T-{date}-{seq}"` (query max id for day or use uuid short form `T-YYYYMMDD-` + 3-digit counter from DB count).
2. Save uploads.
3. Load all configs → JSON snapshot → `progress_total = len(snapshot)`.
4. Insert task `status=running`.
5. Call `scheduler.start_task(task_id)` (implement stub in Task 5/6 that no-ops or completes instantly for now).

For this task only, after create set status running and leave scheduler as:

```python
# app/services/scheduler.py stub
async def start_task(task_id: str) -> None:
    return None
```

List/get endpoints return task + results.

- [ ] **Step 4: Reject bad extensions**

Add test:

```python
@pytest.mark.asyncio
async def test_reject_bad_extension(client):
    files = {
        "tender_file": ("tender.txt", io.BytesIO(b"x"), "text/plain"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={}, files=files)
    assert r.status_code == 400
```

- [ ] **Step 5: Run tests PASS, commit**

```bash
../.venv/bin/pytest tests/test_tasks.py -v
git add backend && git commit -m "feat: add task create/list/detail and file uploads"
```

---

### Task 5: DiagnosisEngine + MockEngine

**Files:**
- Create: `backend/app/engine/base.py`
- Create: `backend/app/engine/mock.py`
- Create: `backend/app/engine/__init__.py`
- Create: `backend/tests/test_engine.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from app.engine.mock import MockEngine


@pytest.mark.asyncio
async def test_mock_engine_returns_fields():
    engine = MockEngine(delay_seconds=0)
    item = {
        "id": 1,
        "title": "企业资质核验",
        "technique": "对照要求",
        "content_mode": "description",
        "content_text": "所有资质文件",
        "importance": "high",
    }
    result = await engine.diagnose_item(task_id="T-1", config_item=item, documents={})
    assert result.content_title == "企业资质核验"
    assert result.result in ("通过", "风险", "缺失")
    assert result.evidence
    assert result.suggestion is not None
```

- [ ] **Step 2: Implement protocol + mock**

`base.py`:

```python
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class DiagnosisItemResult:
    content_title: str
    description: str
    result: str
    evidence: str
    suggestion: str
    config_id: int | None = None


class DiagnosisEngine(Protocol):
    async def diagnose_item(
        self,
        task_id: str,
        config_item: dict[str, Any],
        documents: dict[str, str],
    ) -> DiagnosisItemResult: ...
```

`mock.py`: deterministic hash of title → pick result; `asyncio.sleep(delay)`; fill description from technique/content_text.

- [ ] **Step 3: Pass tests, commit**

```bash
../.venv/bin/pytest tests/test_engine.py -v
git commit -am "feat: add DiagnosisEngine protocol and MockEngine"
```

---

### Task 6: Scheduler (pause / resume / stop)

**Files:**
- Create: `backend/app/services/scheduler.py`
- Create: `backend/tests/test_scheduler.py`
- Modify: `backend/app/api/tasks.py` (wire start + control endpoints)

- [ ] **Step 1: Write scheduler behavior tests** (use MockEngine delay 0.05, 3 config items)

Cover:

1. start → eventually `completed`, `progress_done == progress_total`, results length matches
2. pause mid-way → status `paused`, resume → `completed`
3. stop → status `stopped`, cannot resume (409)
4. pause on completed → 409

Implement control flags in-memory dict `task_id -> {pause_event, stop_flag}` plus DB status updates.

Core loop sketch:

```python
for idx, item in enumerate(snapshot):
    await _wait_if_paused(task_id)
    if _should_stop(task_id):
        await _set_status(task_id, "stopped")
        return
    result = await engine.diagnose_item(...)
    await _persist_result(...)
    await _update_progress(...)
await _generate_reports(...)
await _set_status(task_id, "completed")
```

API:

- `POST /api/tasks/{id}/pause`
- `POST /api/tasks/{id}/resume`
- `POST /api/tasks/{id}/stop`

- [ ] **Step 2: Implement + pass tests**

- [ ] **Step 3: Commit**

```bash
git commit -am "feat: add async diagnosis scheduler with pause/resume/stop"
```

---

### Task 7: Report generation (Markdown + DOCX) + download

**Files:**
- Create: `backend/app/services/report.py`
- Create: `backend/tests/test_report.py`
- Modify: `backend/app/services/scheduler.py` (call on complete)
- Modify: `backend/app/api/tasks.py` (download endpoints)

- [ ] **Step 1: Test report builders**

```python
from app.services.report import build_markdown, write_docx


def test_build_markdown_contains_titles():
    results = [
        {"content_title": "资质", "description": "d", "result": "风险", "evidence": "e", "suggestion": "s"},
    ]
    md = build_markdown("T-1", results)
    assert "# 标书诊断报告" in md
    assert "资质" in md


def test_write_docx(tmp_path):
    path = tmp_path / "r.docx"
    write_docx(str(path), "# 标书诊断报告\n\n你好")
    assert path.exists() and path.stat().st_size > 0
```

- [ ] **Step 2: Implement `build_markdown` + `write_docx`** (python-docx paragraphs from md lines; simple: split by `\n`, headings if startswith `#`)

- [ ] **Step 3: On completed, write `reports/{task_id}/report.md` and `report.docx`, update paths**

- [ ] **Step 4: Download routes**

- `GET /api/tasks/{id}/report.docx` — 404 unless `completed` and file exists
- `GET /api/tasks/{id}/files/{kind}` — `kind` in `tender|bid`
- Detail response includes `report_markdown` string (read md file if exists, else empty) for frontend preview

- [ ] **Step 5: Integration assert: after scheduler completes, download 200**

- [ ] **Step 6: Commit**

```bash
git commit -am "feat: generate markdown/docx reports and download endpoints"
```

---

### Task 8: Seed configs + backend README run command

**Files:**
- Create: `backend/app/seed.py`
- Modify: `backend/app/main.py` (call seed if no configs)
- Create: `README.md` (root)

Seed 3 demo configs matching the design mockups (资质、目录、偏差表).

Startup: if `select count == 0`, insert seeds.

Root README:

```bash
# backend
.venv/bin/uvicorn app.main:app --reload --app-dir backend --port 8000

# frontend (after Task 9)
cd frontend && npm run dev
```

Commit: `docs: add seed data and run instructions`

---

### Task 9: Frontend scaffold

**Files:**
- Create: `frontend/` via Vite

- [ ] **Step 1: Scaffold**

```bash
cd /Users/tongqianni/xlab/tender_application
npm create vite@latest frontend -- --template react
cd frontend && npm install && npm install react-router-dom
```

- [ ] **Step 2: `vite.config.js` proxy**

```js
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
```

- [ ] **Step 3: `src/api.js`** — thin wrappers: `listTasks`, `createTask(FormData)`, `getTask`, `listConfigs`, `createConfig`, `updateConfig`, `deleteConfig`, `pauseTask`, `resumeTask`, `stopTask`, download URLs as string helpers.

- [ ] **Step 4: `App.jsx` routes**

```jsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import TaskListPage from "./pages/TaskListPage";
import TaskDetailPage from "./pages/TaskDetailPage";
import AdminLayout from "./pages/admin/AdminLayout";
import ConfigsPage from "./pages/admin/ConfigsPage";
import AdminTasksPage from "./pages/admin/AdminTasksPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<TaskListPage />} />
        <Route path="/tasks/:id" element={<TaskDetailPage />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="configs" replace />} />
          <Route path="configs" element={<ConfigsPage />} />
          <Route path="tasks" element={<AdminTasksPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

Stub empty page components that render the route name.

- [ ] **Step 5: Commit**

```bash
git add frontend
git commit -m "chore: scaffold React frontend with routes and API client"
```

---

### Task 10: Task list page + create modal

**Files:**
- Create: `frontend/src/components/TaskCard.jsx`
- Create: `frontend/src/components/CreateTaskModal.jsx`
- Modify: `frontend/src/pages/TaskListPage.jsx`
- Add basic CSS in `frontend/src/App.css` (clean layout; avoid purple gradient clichés)

- [ ] **Step 1: TaskCard** shows tender/bid names, id, status badge, created_at; if `completed`, link/button to `/api/tasks/{id}/report.docx`

- [ ] **Step 2: CreateTaskModal** — file inputs, background, requirements, submit FormData to `createTask`, on success navigate to `/tasks/{id}` or refresh list

- [ ] **Step 3: TaskListPage** — fetch list on mount + poll every 3s; header link to `/admin`; grid of cards

- [ ] **Step 4: Manual smoke** — start backend + frontend, create a task with two files, see card appear as 诊断中/已完成

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: task list and create diagnosis UI"
```

---

### Task 11: Task detail page

**Files:**
- Create: `frontend/src/components/ResultTable.jsx`
- Create: `frontend/src/components/MarkdownPreview.jsx`
- Modify: `frontend/src/pages/TaskDetailPage.jsx`

- [ ] **Step 1: MarkdownPreview** — minimal safe renderer: escape HTML, convert `# ` / `## ` lines and `- ` lists to elements (no heavy deps required). Or add `react-markdown` if preferred: `npm install react-markdown`.

Recommended: `npm install react-markdown` for fidelity.

- [ ] **Step 2: ResultTable** columns: 诊断内容、诊断描述、结果、证据、建议

- [ ] **Step 3: Detail page** — load task, show files (links to download), background/requirements, status, download button if completed, markdown from `report_markdown`, table from `results`; poll while running/paused

- [ ] **Step 4: Manual smoke + commit**

```bash
git commit -am "feat: task detail with markdown report and result table"
```

---

### Task 12: Admin configs page

**Files:**
- Modify: `frontend/src/pages/admin/AdminLayout.jsx`
- Modify: `frontend/src/pages/admin/ConfigsPage.jsx`

- [ ] **Step 1: AdminLayout** — left nav: 诊断项目配置 → `/admin/configs`, 诊断任务 → `/admin/tasks`, link back to `/`

- [ ] **Step 2: ConfigsPage** — table + 新增/编辑表单（modal）：fields per spec; delete with confirm

- [ ] **Step 3: Manual smoke + commit**

```bash
git commit -am "feat: admin diagnosis config CRUD UI"
```

---

### Task 13: Admin tasks page with progress & controls

**Files:**
- Modify: `frontend/src/pages/admin/AdminTasksPage.jsx`

- [ ] **Step 1: List tasks with progress bar (`progress_done/progress_total`)**

- [ ] **Step 2: Buttons by status** — running: 暂停/停止; paused: 继续/停止; terminal: none

- [ ] **Step 3: Poll every 2s; wire pause/resume/stop API**

- [ ] **Step 4: Manual smoke of pause → resume → complete; stop → no download**

- [ ] **Step 5: Commit**

```bash
git commit -am "feat: admin task progress and control buttons"
```

---

### Task 14: End-to-end acceptance checklist & polish

**Files:**
- Modify: `README.md`
- Possibly: small CSS polish, empty states

- [ ] **Step 1: Run full acceptance from spec §11**

1. Config CRUD works  
2. Create → running → progress → completed → preview + DOCX download  
3. Pause → resume → complete  
4. Stop → cannot resume, report download 404  
5. Bad file extension rejected  

- [ ] **Step 2: Ensure `.gitignore` covers `uploads/`, `reports/`, `*.db`, `node_modules/`, `.venv/`**

- [ ] **Step 3: Final commit**

```bash
git commit -am "docs: finalize README and acceptance notes"
```

---

## Spec Coverage Self-Review

| Spec requirement | Task(s) |
|---|---|
| FastAPI + React Vite, no auth | 1, 9 |
| SQLite + uploads/reports | 2, 4, 7 |
| Card list fields + create + download when completed | 4, 7, 10 |
| Detail: files, MD preview, result table | 7, 11 |
| Admin config CRUD (title/technique/content/importance) | 3, 12 |
| Admin tasks progress + pause/resume/stop | 6, 13 |
| DiagnosisEngine + MockEngine pluggable | 5 |
| config_snapshot on create | 4 |
| Startup recover running/paused → stopped | 2 |
| DOCX download only when completed | 7 |
| PDF/DOCX upload validation 50MB | 4 |
| Seed + two-command run | 8, 9, README |

## Placeholder / consistency check

- Status strings consistently: `running` | `paused` | `completed` | `stopped` | `failed`
- Result display labels: `通过` | `风险` | `缺失` (MockEngine)
- API prefixes all under `/api`
- No TBD left in steps

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-tender-diagnosis-demo.md`.

**Two execution options:**

1. **Subagent-Driven（推荐）** — 每个 Task 派一个新子代理，Task 之间做审查，迭代快  
2. **Inline Execution** — 在本会话用 executing-plans 按批次执行并设检查点  

选哪种方式？
