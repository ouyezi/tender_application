# 诊断分步控制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建诊断改为草稿模式；任务详情页提供分步按钮（生成诊断项 / 标书索引 / 诊断 / 一键诊断 / 暂停继续），后端按 lane 拆分 scheduler 并暴露 action API。

**Architecture:** 创建任务时 `status=draft` 且不入队 ParseJob；新增 `task_readiness` 模块推导步骤就绪；`scheduler.py` 拆成 checklist / bid-index / diagnosis / full 四条 lane，共享 `_TaskControl.pause_event`；前端 `TaskDetailPage` 根据 `readiness` 控制按钮。

**Tech Stack:** FastAPI、SQLAlchemy 2.x async、asyncio、pytest/httpx；React 18、Vite、fetch。

**Spec:** `docs/superpowers/specs/2026-07-21-diagnosis-stepwise-control-design.md`

---

## File Structure

```text
backend/app/services/workspace.py           # MODIFY: register_task_documents(enqueue_parse=...)
backend/app/services/task_readiness.py    # NEW: compute readiness + bid_index_required
backend/app/services/scheduler.py         # MODIFY: lane split, pause/resume extend, action starters
backend/app/schemas.py                    # MODIFY: TaskReadinessOut, TaskOut.readiness
backend/app/api/tasks.py                  # MODIFY: draft create + 4 action endpoints + readiness GET
backend/app/db.py                         # MODIFY: recover indexing_bid on startup
backend/tests/test_workspace_register.py  # NEW: enqueue_parse=False test
backend/tests/test_task_readiness.py      # NEW: readiness unit tests
backend/tests/test_task_actions.py        # NEW: action API tests
backend/tests/test_scheduler.py           # MODIFY: call run-full where needed
backend/tests/test_tasks.py               # MODIFY: expect draft on create

frontend/src/api.js                       # MODIFY: action + readiness helpers
frontend/src/components/CreateTaskModal.jsx  # MODIFY: button label
frontend/src/components/TaskCard.jsx      # MODIFY: draft badge
frontend/src/pages/TaskDetailPage.jsx     # MODIFY: step bar + action buttons
frontend/src/App.css                      # MODIFY: detail-actions styles
```

---

### Task 1: workspace — `enqueue_parse` 参数

**Files:**
- Modify: `backend/app/services/workspace.py`
- Create: `backend/tests/test_workspace_register.py`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_workspace_register.py`：

```python
import io

import pytest
from sqlalchemy import select

from app.models import ParseJob, WorkspaceFile


def _pdf_bytes():
    return b"%PDF-1.4 fake"


@pytest.mark.asyncio
async def test_register_task_documents_without_enqueue_parse(client):
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={"background": "bg"}, files=files)
    assert r.status_code == 201
    task_id = r.json()["id"]
    assert r.json()["status"] == "draft"

    from app.db import SessionLocal

    async with SessionLocal() as session:
        jobs = (
            await session.execute(select(ParseJob).where(ParseJob.task_id == task_id))
        ).scalars().all()
        assert jobs == []

        wfs = (
            await session.execute(
                select(WorkspaceFile).where(WorkspaceFile.task_id == task_id)
            )
        ).scalars().all()
        assert len(wfs) == 2
        assert all(wf.parse_status == "pending" for wf in wfs)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/tongqianni/xlab/tender_application
.venv/bin/python -m pytest backend/tests/test_workspace_register.py::test_register_task_documents_without_enqueue_parse -v
```

Expected: FAIL — `status` 不是 `draft`，或存在 ParseJob。

- [ ] **Step 3: Implement draft create + enqueue_parse flag**

`backend/app/services/workspace.py` — 修改 `register_task_documents`：

```python
async def register_task_documents(
    session: AsyncSession,
    *,
    task_id: str,
    tender_path: str,
    tender_filename: str,
    bid_path: str,
    bid_filename: str,
    enqueue_parse: bool = True,
) -> tuple[WorkspaceFile, WorkspaceFile]:
    # ... existing setup ...
    for label, path, filename, role in pairs:
        # ... create WorkspaceFile ...
        if enqueue_parse:
            await enqueue_parse(session, wf)
        created.append(wf)
        # ... set tender_file_id / bid_file_id ...
```

`backend/app/api/tasks.py` — 修改 `create_task`：

```python
    task = DiagnosisTask(
        id=task_id,
        # ... existing fields ...
        status="draft",
        progress_done=0,
        progress_total=0,
        config_snapshot=json.dumps(snapshot, ensure_ascii=False),
    )
    # ... commit ...

    await workspace.register_task_documents(
        db,
        task_id=task_id,
        tender_path=tender_path,
        tender_filename=tender_filename,
        bid_path=bid_path,
        bid_filename=bid_filename,
        enqueue_parse=False,
    )
    await db.commit()
    await db.refresh(task)

    await get_tracker(task_id).init_graph()
    # 删除: await parse_scheduler.kick()
    # 删除: await scheduler.start_task(task_id)

    return _task_to_out(task, results=[])
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest backend/tests/test_workspace_register.py::test_register_task_documents_without_enqueue_parse -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workspace.py backend/app/api/tasks.py backend/tests/test_workspace_register.py
git commit -m "feat: create diagnosis tasks in draft without parse enqueue"
```

---

### Task 2: readiness schema + service

**Files:**
- Modify: `backend/app/schemas.py`
- Create: `backend/app/services/task_readiness.py`
- Create: `backend/tests/test_task_readiness.py`

- [ ] **Step 1: Write the failing test**

创建 `backend/tests/test_task_readiness.py`：

```python
import io

import pytest

from app.services.task_readiness import compute_task_readiness


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_draft(client):
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={}, files=files)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_readiness_for_fresh_draft(client):
    task_id = await _create_draft(client)
    readiness = await compute_task_readiness(task_id)
    assert readiness["checklist_ready"] is False
    assert readiness["bid_index_ready"] is False
    assert readiness["bid_index_required"] is True
    assert readiness["diagnosis_ready"] is False
    assert readiness["checklist_lane_active"] is False
    assert readiness["bid_index_lane_active"] is False
    assert readiness["full_run_active"] is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest backend/tests/test_task_readiness.py::test_readiness_for_fresh_draft -v
```

Expected: FAIL — `ModuleNotFoundError: task_readiness`

- [ ] **Step 3: Add schema + service**

`backend/app/schemas.py` — 在 `TaskListOut` 之前添加：

```python
class TaskReadinessOut(BaseModel):
    checklist_ready: bool
    bid_index_ready: bool
    bid_index_required: bool
    diagnosis_ready: bool
    checklist_lane_active: bool
    bid_index_lane_active: bool
    full_run_active: bool
    diagnosis_lane_active: bool
```

`backend/app/schemas.py` — 扩展 `TaskOut`：

```python
class TaskOut(TaskListOut):
    tender_path: str
    bid_path: str
    results: List[ResultOut] = []
    report_markdown: str = ""
    interpret_markdown: str = ""
    readiness: Optional[TaskReadinessOut] = None
```

创建 `backend/app/services/task_readiness.py`：

```python
from __future__ import annotations

from sqlalchemy import select

from app import db as database
from app.models import DiagnosisTask, IndexJob
from app.services import checklist_service, scheduler
from app.services.checklist_service import ChecklistNotAvailable


async def _bid_index_ready(session, task: DiagnosisTask) -> bool:
    if not task.bid_file_id:
        return False
    result = await session.execute(
        select(IndexJob)
        .where(
            IndexJob.task_id == task.id,
            IndexJob.file_id == task.bid_file_id,
        )
        .order_by(IndexJob.id.desc())
    )
    job = result.scalars().first()
    return job is not None and job.status == "ready"


async def _bid_index_required(task_id: str) -> bool:
    try:
        report = await checklist_service.get_report(task_id)
    except ChecklistNotAvailable:
        return True
    for category in report["categories"]:
        for item in category["items"]:
            if (item.get("diagnosis_mode") or "file") != "offline":
                return True
    return False


async def compute_task_readiness(task_id: str) -> dict:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)

        checklist_ready = task.current_checklist_generation_id is not None
        bid_index_ready = await _bid_index_ready(session, task)

    bid_index_required = await _bid_index_required(task_id)
    lane = scheduler.get_lane_state(task_id)

    diagnosis_ready = checklist_ready and (
        not bid_index_required or bid_index_ready
    )

    return {
        "checklist_ready": checklist_ready,
        "bid_index_ready": bid_index_ready,
        "bid_index_required": bid_index_required,
        "diagnosis_ready": diagnosis_ready,
        "checklist_lane_active": lane["checklist_lane_active"],
        "bid_index_lane_active": lane["bid_index_lane_active"],
        "full_run_active": lane["full_run_active"],
        "diagnosis_lane_active": lane["diagnosis_lane_active"],
    }
```

`backend/app/services/scheduler.py` — 在 `_controls` 区域添加 lane 状态查询（Task 4 会完善 `_TaskControl`，此处先 stub）：

```python
def get_lane_state(task_id: str) -> dict:
    ctrl = _controls.get(task_id)
    if ctrl is None:
        return {
            "checklist_lane_active": False,
            "bid_index_lane_active": False,
            "full_run_active": False,
            "diagnosis_lane_active": False,
        }
    return ctrl.lane_state()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest backend/tests/test_task_readiness.py::test_readiness_for_fresh_draft -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/services/task_readiness.py backend/tests/test_task_readiness.py backend/app/services/scheduler.py
git commit -m "feat: add task readiness schema and compute service"
```

---

### Task 3: scheduler lane control refactor

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/app/services/workspace.py`（添加 `ensure_file_parse_enqueued`）
- Modify: `backend/tests/test_scheduler.py`（添加 helper `_start_full`）

- [ ] **Step 1: Write the failing test**

在 `backend/tests/test_scheduler.py` 顶部添加 helper，并在文件末尾添加：

```python
async def _start_full(client, task_id: str) -> None:
    r = await client.post(f"/api/tasks/{task_id}/actions/run-full")
    assert r.status_code == 202


@pytest.mark.asyncio
async def test_start_generate_checklist_from_draft(client, monkeypatch):
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]
    assert body["status"] == "draft"

    r = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    assert r.status_code == 202

    status = await scheduler.wait_for_terminal(task_id, timeout=15)
    assert status in ("draft", "failed")  # draft = idle after checklist lane

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["current_checklist_generation_id"] is not None
    assert detail["interpret_md_path"]
    assert detail["status"] == "draft"
```

同时修改 `_create_task` 断言：`assert body["status"] == "draft"`。

修改 `test_scheduler_runs_to_completion`：

```python
async def test_scheduler_runs_to_completion(client):
    await _seed_configs(client, 3)
    body = await _create_task(client)
    task_id = body["id"]
    await _start_full(client, task_id)

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"
    # ... rest unchanged ...
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest backend/tests/test_scheduler.py::test_start_generate_checklist_from_draft -v
```

Expected: FAIL — 404 on action endpoint 或 status 不对。

- [ ] **Step 3: Refactor `_TaskControl` and lane runners**

`backend/app/services/scheduler.py` — 替换 `_TaskControl`：

```python
PAUSABLE_STATUSES = frozenset(
    {
        "interpreting",
        "generating_checklist",
        "indexing_bid",
        "diagnosing",
        "running",
    }
)
IDLE_STATUS = "draft"
RUNNING_LANE_STATUSES = frozenset(
    {"interpreting", "generating_checklist", "indexing_bid", "diagnosing"}
)


@dataclass
class _TaskControl:
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_requested: bool = False
    checklist_task: Optional[asyncio.Task] = None
    bid_index_task: Optional[asyncio.Task] = None
    diagnosis_task: Optional[asyncio.Task] = None
    full_task: Optional[asyncio.Task] = None
    paused_from_status: Optional[str] = None
    resume_mode: Optional[str] = None  # "checklist" | "bid_index" | "diagnosis" | "full"
    done_event: asyncio.Event = field(default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if not self.pause_event.is_set():
            self.pause_event.set()

    def lane_state(self) -> dict:
        return {
            "checklist_lane_active": self.checklist_task is not None
            and not self.checklist_task.done(),
            "bid_index_lane_active": self.bid_index_task is not None
            and not self.bid_index_task.done(),
            "diagnosis_lane_active": self.diagnosis_task is not None
            and not self.diagnosis_task.done(),
            "full_run_active": self.full_task is not None and not self.full_task.done(),
        }

    def any_lane_active(self) -> bool:
        state = self.lane_state()
        return any(state.values())
```

添加 parse enqueue helper 到 `workspace.py`：

```python
async def ensure_file_parse_enqueued(session: AsyncSession, file_id: str) -> bool:
    wf = await session.get(WorkspaceFile, file_id)
    if wf is None:
        raise LookupError(file_id)
    if wf.parse_status == "succeeded":
        return False
    existing = (
        await session.execute(
            select(ParseJob).where(
                ParseJob.file_id == file_id,
                ParseJob.status.in_(("queued", "running")),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    await enqueue_parse(session, wf)
    return True
```

在 `scheduler.py` 提取并新增（从现有 `_run` 复制 interpret + checklist 段，去掉 `_complete_from_diagnosis` 调用）：

```python
async def _set_idle(task_id: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None or task.status in TERMINAL_STATUSES:
            return
        if task.status == "paused":
            return
        task.status = IDLE_STATUS
        task.updated_at = utcnow()
        await session.commit()


async def _run_checklist_lane(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                return
            if task.current_checklist_generation_id is not None:
                return
            if task.tender_file_id:
                enqueued = await workspace.ensure_file_parse_enqueued(
                    session, task.tender_file_id
                )
                if enqueued:
                    await session.commit()
                    await parse_scheduler.kick()

        # ... copy need_interpret block from _run (lines ~655-728) ...
        # ... copy need_checklist block from _run (lines ~730-774) ...
        # DO NOT call _complete_from_diagnosis

        await _set_idle(task_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # reuse _handle_checklist_failure / _mark_failed paths
        ...
    finally:
        ctrl.checklist_task = None


async def _run_bid_index_lane(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                return
            if not task.bid_file_id:
                await _mark_failed(task_id, "bid_file_missing", "bid_index")
                return
            task.status = "indexing_bid"
            task.updated_at = utcnow()
            enqueued = await workspace.ensure_file_parse_enqueued(
                session, task.bid_file_id
            )
            await session.commit()
            if enqueued:
                await parse_scheduler.kick()

        await _wait_if_paused(task_id)
        if _should_stop(task_id):
            await _mark_stopped(task_id)
            return

        try:
            await wait_for_bid_index_ready(task_id)
        except BidIndexBlockedError as exc:
            await _mark_failed(task_id, str(exc), "bid_index")
            return

        await _set_idle(task_id)
    finally:
        ctrl.bid_index_task = None


async def start_generate_checklist(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.lane_state()["checklist_lane_active"]:
        raise SchedulerConflict("task_lane_active")
    if ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")
        if task.current_checklist_generation_id is not None:
            raise SchedulerConflict("step_already_completed")

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.checklist_task = asyncio.create_task(_run_checklist_lane(task_id))


async def start_index_bid(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.lane_state()["bid_index_lane_active"]:
        raise SchedulerConflict("task_lane_active")
    if ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")
        if await _bid_index_ready_in_session(session, task):
            raise SchedulerConflict("step_already_completed")

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.bid_index_task = asyncio.create_task(_run_bid_index_lane(task_id))
```

将 `_run` 重命名为 `_run_full`，内部逻辑：

```python
async def _run_full(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        readiness = await compute_task_readiness(task_id)
        tasks = []
        if not readiness["checklist_ready"]:
            if not ctrl.lane_state()["checklist_lane_active"]:
                tasks.append(asyncio.create_task(_run_checklist_lane(task_id)))
        if readiness["bid_index_required"] and not readiness["bid_index_ready"]:
            if not ctrl.lane_state()["bid_index_lane_active"]:
                tasks.append(asyncio.create_task(_run_bid_index_lane(task_id)))
        if tasks:
            await asyncio.gather(*tasks)

        readiness = await compute_task_readiness(task_id)
        if not readiness["diagnosis_ready"]:
            if not readiness["checklist_ready"]:
                return
            if readiness["bid_index_required"] and not readiness["bid_index_ready"]:
                return

        await _complete_from_diagnosis(task_id)
    finally:
        ctrl.full_task = None


async def start_run_full(task_id: str) -> None:
    ctrl = _get_control(task_id)
    if ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")
    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.full_task = asyncio.create_task(_run_full(task_id))


async def start_diagnose(task_id: str) -> None:
    from app.services.task_readiness import compute_task_readiness

    ctrl = _get_control(task_id)
    if ctrl.lane_state()["diagnosis_lane_active"] or ctrl.lane_state()["full_run_active"]:
        raise SchedulerConflict("task_lane_active")

    readiness = await compute_task_readiness(task_id)
    if not readiness["checklist_ready"]:
        raise SchedulerConflict("checklist_not_ready")
    if readiness["bid_index_required"] and not readiness["bid_index_ready"]:
        raise SchedulerConflict("bid_index_not_ready")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status in TERMINAL_STATUSES:
            raise SchedulerConflict("invalid_task_status")

    async def _runner() -> None:
        try:
            await _complete_from_diagnosis(task_id)
        finally:
            ctrl.diagnosis_task = None

    ctrl.stop_requested = False
    ctrl.pause_event.set()
    ctrl.diagnosis_task = asyncio.create_task(_runner())
```

更新 `pause_task` / `resume_task`：

```python
async def pause_task(task_id: str) -> DiagnosisTask:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status not in PAUSABLE_STATUSES:
            raise SchedulerConflict(f"cannot pause task in status {task.status}")
        paused_from = task.status
        task.status = "paused"
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        paused = task

    ctrl = _get_control(task_id)
    ctrl.paused_from_status = paused_from
    ctrl.pause_event.clear()
    await get_tracker(task_id).notify("diagnosis", meta={"paused": True})
    return paused


async def resume_task(task_id: str) -> DiagnosisTask:
    ctrl = _get_control(task_id)
    if ctrl.stop_requested:
        raise SchedulerConflict("cannot resume task after stop was requested")

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status != "paused":
            raise SchedulerConflict(f"cannot resume task in status {task.status}")
        restored = ctrl.paused_from_status or IDLE_STATUS
        task.status = restored
        task.updated_at = utcnow()
        await session.commit()
        await session.refresh(task)
        resumed = task

    ctrl.pause_event.set()
    mode = ctrl.resume_mode
    if mode == "full" and (ctrl.full_task is None or ctrl.full_task.done()):
        ctrl.full_task = asyncio.create_task(_run_full(task_id))
    elif mode == "checklist" and (ctrl.checklist_task is None or ctrl.checklist_task.done()):
        ctrl.checklist_task = asyncio.create_task(_run_checklist_lane(task_id))
    elif mode == "bid_index" and (ctrl.bid_index_task is None or ctrl.bid_index_task.done()):
        ctrl.bid_index_task = asyncio.create_task(_run_bid_index_lane(task_id))
    elif restored == "diagnosing" and (ctrl.diagnosis_task is None or ctrl.diagnosis_task.done()):
        ctrl.diagnosis_task = asyncio.create_task(_complete_from_diagnosis(task_id))
    return resumed
```

在 `_wait_if_paused` 中已足够；在 `wait_for_bid_index_ready` 循环里每轮调用 `_wait_if_paused(task_id)`（修改 `bid_index_wait.py`）。

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/test_scheduler.py::test_start_generate_checklist_from_draft backend/tests/test_scheduler.py::test_scheduler_runs_to_completion -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduler.py backend/app/services/workspace.py backend/app/services/bid_index_wait.py backend/tests/test_scheduler.py
git commit -m "feat: split scheduler into checklist, bid-index, and full lanes"
```

---

### Task 4: action API endpoints

**Files:**
- Modify: `backend/app/api/tasks.py`
- Create: `backend/tests/test_task_actions.py`
- Modify: `backend/tests/test_tasks.py`

- [ ] **Step 1: Write the failing tests**

创建 `backend/tests/test_task_actions.py`：

```python
import asyncio
import io

import pytest


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_draft(client):
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
        "bid_file": ("bid.docx", io.BytesIO(b"PK fake"), "application/octet-stream"),
    }
    r = await client.post("/api/tasks", data={}, files=files)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_diagnose_requires_checklist(client):
    task_id = await _create_draft(client)
    r = await client.post(f"/api/tasks/{task_id}/actions/diagnose")
    assert r.status_code == 409
    assert "checklist_not_ready" in r.text


@pytest.mark.asyncio
async def test_parallel_actions_both_accepted(client):
    task_id = await _create_draft(client)
    r1 = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    r2 = await client.post(f"/api/tasks/{task_id}/actions/index-bid")
    assert r1.status_code == 202
    assert r2.status_code == 202


@pytest.mark.asyncio
async def test_duplicate_checklist_action_conflict(client):
    task_id = await _create_draft(client)
    r1 = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    assert r1.status_code == 202
    await asyncio.sleep(0.05)
    r2 = await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")
    assert r2.status_code == 409
    assert "task_lane_active" in r2.text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest backend/tests/test_task_actions.py -v
```

Expected: FAIL — 404 on action routes

- [ ] **Step 3: Implement endpoints**

`backend/app/api/tasks.py`：

```python
from app.schemas import ChecklistReportOut, ExecutionGraphOut, TaskListOut, TaskOut, TaskReadinessOut
from app.services.task_readiness import compute_task_readiness

def _action_response(task_id: str, status: str) -> dict:
    return {"task_id": task_id, "status": status}


async def _load_task_out_with_readiness(db: AsyncSession, task_id: str) -> TaskOut:
    out = await _load_task_out(db, task_id)
    readiness = await compute_task_readiness(task_id)
    payload = out.model_dump()
    payload["readiness"] = readiness
    return TaskOut.model_validate(payload)


@router.get("/{task_id}/readiness", response_model=TaskReadinessOut)
async def get_task_readiness(task_id: str, db: AsyncSession = Depends(get_db)) -> TaskReadinessOut:
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return TaskReadinessOut.model_validate(await compute_task_readiness(task_id))


@router.post("/{task_id}/actions/generate-checklist", status_code=status.HTTP_202_ACCEPTED)
async def action_generate_checklist(task_id: str) -> dict:
    try:
        await scheduler.start_generate_checklist(task_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _action_response(task_id, "generating_checklist")


@router.post("/{task_id}/actions/index-bid", status_code=status.HTTP_202_ACCEPTED)
async def action_index_bid(task_id: str) -> dict:
    try:
        await scheduler.start_index_bid(task_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _action_response(task_id, "indexing_bid")


@router.post("/{task_id}/actions/diagnose", status_code=status.HTTP_202_ACCEPTED)
async def action_diagnose(task_id: str) -> dict:
    try:
        await scheduler.start_diagnose(task_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _action_response(task_id, "diagnosing")


@router.post("/{task_id}/actions/run-full", status_code=status.HTTP_202_ACCEPTED)
async def action_run_full(task_id: str) -> dict:
    try:
        await scheduler.start_run_full(task_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _action_response(task_id, "running")
```

修改 `get_task` 返回 readiness（调用 `_load_task_out_with_readiness`）。

更新 `backend/tests/test_tasks.py`：

```python
    assert body["status"] == "draft"
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest backend/tests/test_task_actions.py backend/tests/test_tasks.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/tasks.py backend/tests/test_task_actions.py backend/tests/test_tasks.py
git commit -m "feat: add stepwise task action API endpoints"
```

---

### Task 5: startup recovery + pause in bid_index_wait

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/services/bid_index_wait.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

在 `backend/tests/test_scheduler.py` 添加：

```python
@pytest.mark.asyncio
async def test_pause_during_generating_checklist(client, monkeypatch):
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]
    await client.post(f"/api/tasks/{task_id}/actions/generate-checklist")

    for _ in range(50):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["status"] in ("generating_checklist", "interpreting"):
            break
        await asyncio.sleep(0.02)

    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest backend/tests/test_scheduler.py::test_pause_during_generating_checklist -v
```

Expected: FAIL — 409 cannot pause

- [ ] **Step 3: Implement recovery + pause hook**

`backend/app/db.py` — `recover_interrupted_tasks` 增加 `"indexing_bid"`：

```python
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
```

`backend/app/services/bid_index_wait.py` — 在 poll 循环内：

```python
from app.services.scheduler import _should_stop, _wait_if_paused

# inside while True, before sleep:
            await _wait_if_paused(task_id)
            if _should_stop(task_id):
                raise BidIndexBlockedError("task_stopped")
```

- [ ] **Step 4: Run test**

```bash
.venv/bin/python -m pytest backend/tests/test_scheduler.py::test_pause_during_generating_checklist -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/services/bid_index_wait.py backend/tests/test_scheduler.py
git commit -m "feat: extend pause/resume and recover indexing_bid on startup"
```

---

### Task 6: frontend API helpers

**Files:**
- Modify: `frontend/src/api.js`

- [ ] **Step 1: Add functions**

```javascript
export function generateChecklist(id) {
  return request(`/api/tasks/${id}/actions/generate-checklist`, { method: 'POST' })
}

export function indexBid(id) {
  return request(`/api/tasks/${id}/actions/index-bid`, { method: 'POST' })
}

export function runDiagnosis(id) {
  return request(`/api/tasks/${id}/actions/diagnose`, { method: 'POST' })
}

export function runFullDiagnosis(id) {
  return request(`/api/tasks/${id}/actions/run-full`, { method: 'POST' })
}

export function getTaskReadiness(id) {
  return request(`/api/tasks/${id}/readiness`)
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api.js
git commit -m "feat: add stepwise diagnosis action API helpers"
```

---

### Task 7: frontend create + list draft badge

**Files:**
- Modify: `frontend/src/components/CreateTaskModal.jsx`
- Modify: `frontend/src/components/TaskCard.jsx`
- Modify: `frontend/src/pages/TaskDetailPage.jsx`（STATUS_LABELS）

- [ ] **Step 1: Update labels**

`CreateTaskModal.jsx` 第 135 行：

```jsx
{submitting ? '提交中…' : '创建'}
```

`TaskCard.jsx` STATUS_LABELS 添加：

```javascript
  draft: '待执行',
  indexing_bid: '标书索引中',
```

`TaskDetailPage.jsx` STATUS_LABELS 同步添加上述两项。

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CreateTaskModal.jsx frontend/src/components/TaskCard.jsx frontend/src/pages/TaskDetailPage.jsx
git commit -m "feat: show draft and indexing_bid status labels"
```

---

### Task 8: TaskDetailPage action button area

**Files:**
- Modify: `frontend/src/pages/TaskDetailPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: Add step bar + buttons after file meta block**

在 `TaskDetailPage.jsx` 添加 import：

```javascript
import {
  fileUrl,
  generateChecklist,
  getTask,
  indexBid,
  interpretHtmlUrl,
  pauseTask,
  reportDocxUrl,
  resumeTask,
  runDiagnosis,
  runFullDiagnosis,
} from '../api'
```

添加 state：

```javascript
const [actionError, setActionError] = useState('')
const [actionLoading, setActionLoading] = useState('')
```

添加 helper（组件内）：

```javascript
const readiness = task.readiness || {}
const terminal = new Set(['completed', 'stopped', 'failed'])
const isTerminal = terminal.has(status)
const isPaused = status === 'paused'
const isRunning = !isTerminal && !isPaused && status !== 'draft'

const canGenerateChecklist =
  !isTerminal &&
  !readiness.checklist_ready &&
  !readiness.checklist_lane_active &&
  !readiness.full_run_active

const canIndexBid =
  !isTerminal &&
  !readiness.bid_index_ready &&
  !readiness.bid_index_lane_active &&
  !readiness.full_run_active

const canDiagnose =
  !isTerminal &&
  readiness.diagnosis_ready &&
  status !== 'completed' &&
  !readiness.diagnosis_lane_active &&
  !readiness.full_run_active

const canRunFull =
  !isTerminal && !readiness.full_run_active

const canPause = isRunning
const canResume = isPaused

async function runAction(key, fn) {
  setActionError('')
  setActionLoading(key)
  try {
    await fn(id)
    await load(true)
  } catch (err) {
    setActionError(err.message || '操作失败')
  } finally {
    setActionLoading('')
  }
}
```

在文件区 `detail-meta` 之后插入：

```jsx
        <div className="detail-step-bar">
          <div className={`detail-step${readiness.checklist_ready ? ' is-done' : ''}`}>
            <span className="detail-step-dot" />
            <span className="detail-step-title">诊断项</span>
            <span className="detail-step-status">
              {readiness.checklist_lane_active
                ? '生成中'
                : readiness.checklist_ready
                  ? '已生成'
                  : '待执行'}
            </span>
          </div>
          <div className={`detail-step${readiness.bid_index_ready ? ' is-done' : ''}`}>
            <span className="detail-step-dot" />
            <span className="detail-step-title">标书索引</span>
            <span className="detail-step-status">
              {readiness.bid_index_lane_active || status === 'indexing_bid'
                ? '索引中'
                : readiness.bid_index_ready
                  ? '已就绪'
                  : readiness.bid_index_required
                    ? '待执行'
                    : '无需索引'}
            </span>
          </div>
          <div className={`detail-step${status === 'completed' ? ' is-done' : ''}`}>
            <span className="detail-step-dot" />
            <span className="detail-step-title">诊断</span>
            <span className="detail-step-status">
              {status === 'completed'
                ? '已完成'
                : readiness.diagnosis_lane_active || status === 'diagnosing'
                  ? '诊断中'
                  : '待执行'}
            </span>
          </div>
        </div>

        <div className="detail-actions">
          <div className="detail-actions-group">
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canGenerateChecklist || Boolean(actionLoading)}
              onClick={() => runAction('checklist', () => generateChecklist(id))}
            >
              {actionLoading === 'checklist' ? '生成中…' : '生成诊断项'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canIndexBid || Boolean(actionLoading)}
              onClick={() => runAction('index', () => indexBid(id))}
            >
              {actionLoading === 'index' ? '索引中…' : '标书索引'}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canDiagnose || Boolean(actionLoading)}
              onClick={() => runAction('diagnose', () => runDiagnosis(id))}
            >
              {actionLoading === 'diagnose' ? '诊断中…' : '诊断'}
            </button>
          </div>
          <div className="detail-actions-group">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canRunFull || Boolean(actionLoading)}
              onClick={() => runAction('full', () => runFullDiagnosis(id))}
            >
              {actionLoading === 'full' ? '执行中…' : '一键诊断'}
            </button>
            {canPause && (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={Boolean(actionLoading)}
                onClick={() => runAction('pause', () => pauseTask(id))}
              >
                暂停
              </button>
            )}
            {canResume && (
              <button
                type="button"
                className="btn btn-secondary"
                disabled={Boolean(actionLoading)}
                onClick={() => runAction('resume', () => resumeTask(id))}
              >
                继续
              </button>
            )}
          </div>
        </div>
        {actionError && <p className="page-error">{actionError}</p>}
```

更新轮询：

```javascript
const POLL_STATUSES = new Set([
  'draft',
  'interpreting',
  'generating_checklist',
  'indexing_bid',
  'diagnosing',
  'running',
  'paused',
])

useEffect(() => {
  if (!task) return undefined
  const laneActive = task.readiness && (
    task.readiness.checklist_lane_active ||
    task.readiness.bid_index_lane_active ||
    task.readiness.diagnosis_lane_active ||
    task.readiness.full_run_active
  )
  const shouldPoll =
    POLL_STATUSES.has(task.status) &&
    (task.status !== 'draft' || laneActive)
  if (!shouldPoll) return undefined
  const timer = setInterval(() => load(true), 2000)
  return () => clearInterval(timer)
}, [task?.status, task?.readiness, load])
```

- [ ] **Step 2: Add CSS**

`frontend/src/App.css`：

```css
.detail-step-bar {
  display: flex;
  gap: 1.5rem;
  margin: 1rem 0;
  flex-wrap: wrap;
}

.detail-step {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  min-width: 5rem;
}

.detail-step-dot {
  width: 0.65rem;
  height: 0.65rem;
  border-radius: 50%;
  background: var(--muted, #999);
}

.detail-step.is-done .detail-step-dot {
  background: var(--success, #2e7d32);
}

.detail-step-title {
  font-weight: 600;
  font-size: 0.85rem;
}

.detail-step-status {
  font-size: 0.8rem;
  color: var(--muted-text, #666);
}

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1rem;
  justify-content: space-between;
  margin-top: 0.75rem;
}

.detail-actions-group {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
```

- [ ] **Step 3: Manual smoke test**

```bash
# terminal 1
cd /Users/tongqianni/xlab/tender_application
.venv/bin/python startup.py

# terminal 2
cd frontend && npm run dev
```

验证：创建任务 → 详情页 draft → 点「生成诊断项」/「标书索引」→ 步骤条更新 → 前置完成后「诊断」可用 → 一键诊断 / 暂停 / 继续。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/TaskDetailPage.jsx frontend/src/App.css
git commit -m "feat: add stepwise diagnosis controls on task detail page"
```

---

### Task 9: fix remaining backend tests

**Files:**
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/tests/test_checklist_api.py`（若有 create 后期望自动跑）
- Modify: 其他引用 create task 后期望 `completed` 的测试

- [ ] **Step 1: Find affected tests**

```bash
cd /Users/tongqianni/xlab/tender_application
rg "POST.*\"/api/tasks\"" backend/tests -l
.venv/bin/python -m pytest backend/tests -q --tb=no 2>&1 | tail -20
```

- [ ] **Step 2: Add `_start_full` helper to shared conftest or each file**

任何期望自动完成流水线的测试，在 create 后添加：

```python
await client.post(f"/api/tasks/{task_id}/actions/run-full")
```

- [ ] **Step 3: Run full backend suite**

```bash
.venv/bin/python -m pytest backend/tests -q
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/
git commit -m "test: update task tests for draft-first stepwise flow"
```

---

## Self-Review

**Spec coverage:**

| Spec 要求 | 对应 Task |
|-----------|-----------|
| draft 创建 | Task 1 |
| 4 个 action API | Task 3–4 |
| readiness | Task 2 |
| lane 拆分 + 并行 | Task 3 |
| pause/resume 扩展 | Task 3, 5 |
| 一键 smart resume | Task 3 `_run_full` |
| 前端按钮区 | Task 8 |
| 创建入口文案 | Task 7 |
| draft 徽章 | Task 7 |
| 进程重启 indexing_bid | Task 5 |
| 测试 | Task 1–5, 9 |

**Placeholder scan:** 无 TBD；Task 3 Step 3 中 `# ... copy need_interpret block ...` 为指引性注释，实施时需从现有 `_run` 复制完整代码（约 120 行），不可留空 pass。

**Type consistency:** `TaskReadinessOut` 字段与 `compute_task_readiness` / 前端 `task.readiness` 一致；action 端点均返回 202。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-21-diagnosis-stepwise-control.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每个 Task 派发独立 subagent，任务间 review，迭代快

**2. Inline Execution** — 本会话用 executing-plans 按 Task 批量执行，checkpoint Review

你想用哪种方式？
