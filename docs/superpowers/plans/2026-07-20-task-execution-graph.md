# 任务执行进程图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 `ExecutionGraphTracker` 框架与持久化表，全链路接入 parse/index/scheduler；提供 `GET /api/tasks/{id}/execution-graph`；新增 `/tasks/:id/process`「查看进程」页展示 DAG、状态与耗时。

**Architecture:** 中心化 tracker 模块（`init_graph` + `track()` + `add_node` + `notify`）写入 `execution_nodes` / `execution_edges`；业务 scheduler 仅 1–3 行 hook；观测层不驱动业务决策。前端 React Flow + dagre 消费标准 graph JSON，运行中 2s 轮询，终态静态读 DB。

**Tech Stack:** FastAPI、SQLAlchemy async、SQLite migration（`init_db_on_connection`）、pytest；React 19、Vite、`@xyflow/react`、`@dagrejs/dagre`。

**Spec:** `docs/superpowers/specs/2026-07-20-task-execution-graph-design.md`

---

## File Structure

```text
backend/app/models.py                              # MOD: ExecutionNode, ExecutionEdge
backend/app/db.py                                  # MOD: recover_interrupted → mark graph running→interrupted
backend/app/schemas.py                             # MOD: ExecutionGraphOut, ExecutionNodeOut, ExecutionEdgeOut
backend/app/services/execution_graph/__init__.py   # NEW: get_tracker export
backend/app/services/execution_graph/template.py   # NEW: TASK_GRAPH_NODES, TASK_GRAPH_EDGES
backend/app/services/execution_graph/tracker.py    # NEW: ExecutionGraphTracker
backend/app/services/execution_graph/query.py      # NEW: build_execution_graph_response
backend/app/api/tasks.py                           # MOD: init_graph on POST; GET execution-graph
backend/app/services/parse_scheduler.py            # MOD: track parse.tender/bid
backend/app/services/parse/pipeline.py             # MOD: notify parse_stage (optional thin hook)
backend/app/services/index_scheduler.py            # MOD: track index.* stages + index.gate N/A here
backend/app/services/scheduler.py                  # MOD: interpret/checklist/diagnosis/report hooks
backend/app/services/bid_index_wait.py             # MOD: track index.gate
backend/app/services/task_delete.py                # MOD: delete execution_nodes/edges
backend/tests/test_execution_graph_tracker.py      # NEW
backend/tests/test_execution_graph_api.py          # NEW

frontend/package.json                              # MOD: @xyflow/react, @dagrejs/dagre
frontend/src/api.js                                # MOD: getExecutionGraph
frontend/src/App.jsx                               # MOD: route /tasks/:id/process
frontend/src/pages/TaskProcessPage.jsx             # NEW
frontend/src/pages/TaskDetailPage.jsx              # MOD: link 查看进程
frontend/src/components/execution/ExecutionGraph.jsx       # NEW
frontend/src/components/execution/ExecutionNodeCard.jsx    # NEW
frontend/src/App.css                               # MOD: process page + node status colors
```

---

### Task 1: ExecutionNode / ExecutionEdge 模型

**Files:**
- Modify: `backend/app/models.py`
- Test: `backend/tests/test_execution_graph_tracker.py`（Task 2 一并创建；本 Task 完成后 `create_all` 应含新表）

- [ ] **Step 1: 在 `models.py` 末尾添加模型**

```python
import uuid
from sqlalchemy import Index, UniqueConstraint


class ExecutionNode(Base):
    __tablename__ = "execution_nodes"
    __table_args__ = (
        UniqueConstraint("task_id", "node_key", name="uq_execution_nodes_task_key"),
        Index("ix_execution_nodes_task_status", "task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("diagnosis_tasks.id"), nullable=False, index=True)
    node_key: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="stage")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    meta: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExecutionEdge(Base):
    __tablename__ = "execution_edges"
    __table_args__ = (
        UniqueConstraint("task_id", "from_key", "to_key", name="uq_execution_edges_task_from_to"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("diagnosis_tasks.id"), nullable=False, index=True)
    from_key: Mapped[str] = mapped_column(String(128), nullable=False)
    to_key: Mapped[str] = mapped_column(String(128), nullable=False)
    edge_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="sequential")
```

- [ ] **Step 2: 验证表创建**

Run: `cd backend && ../.venv/bin/python -c "from app.db import init_db_on_connection; from sqlalchemy import create_engine; e=create_engine('sqlite:///:memory:'); init_db_on_connection(e.connect()); print('ok')"`

Expected: `ok`（无 ImportError / 无 duplicate table 错误）

- [ ] **Step 3: Commit**

```bash
git add backend/app/models.py
git commit -m "feat: add execution graph persistence models"
```

---

### Task 2: Graph 模板与 ExecutionGraphTracker

**Files:**
- Create: `backend/app/services/execution_graph/__init__.py`
- Create: `backend/app/services/execution_graph/template.py`
- Create: `backend/app/services/execution_graph/tracker.py`
- Create: `backend/tests/test_execution_graph_tracker.py`

- [ ] **Step 1: 写失败测试 `test_execution_graph_tracker.py`**

```python
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, ExecutionNode
from app.services.execution_graph.tracker import ExecutionGraphTracker, get_tracker


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/eg.db", poolclass=NullPool
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="T-EG-001",
                tender_filename="t.pdf",
                tender_path="/tmp/t.pdf",
                bid_filename="b.docx",
                bid_path="/tmp/b.docx",
                status="interpreting",
            )
        )
        await session.commit()
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_init_graph_creates_static_nodes(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    async with db_session() as session:
        from sqlalchemy import func, select
        count = await session.scalar(
            select(func.count()).select_from(ExecutionNode).where(
                ExecutionNode.task_id == "T-EG-001"
            )
        )
    assert count >= 10
    assert "parse.tender" in {n.node_key for n in await _all_nodes(db_session, "T-EG-001")}


@pytest.mark.asyncio
async def test_track_marks_completed_with_duration(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    async with tracker.track("interpret", label="解读"):
        pass
    node = await _get_node(db_session, "T-EG-001", "interpret")
    assert node.status == "completed"
    assert node.started_at is not None
    assert node.ended_at is not None
    assert node.duration_ms is not None
    assert node.duration_ms >= 0


@pytest.mark.asyncio
async def test_track_failure_marks_failed(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    with pytest.raises(ValueError):
        async with tracker.track("checklist.generate"):
            raise ValueError("boom")
    node = await _get_node(db_session, "T-EG-001", "checklist.generate")
    assert node.status == "failed"
    import json
    assert "boom" in json.loads(node.meta).get("error", "")


@pytest.mark.asyncio
async def test_add_node_dynamic_batch(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    await tracker.add_node(
        node_key="diagnosis.category.c1",
        parent_key="diagnosis",
        label="资质审查",
        kind="batch",
    )
    async with tracker.track_node("diagnosis.category.c1"):
        pass
    node = await _get_node(db_session, "T-EG-001", "diagnosis.category.c1")
    assert node.status == "completed"
    assert node.parent_key == "diagnosis"


async def _all_nodes(session_factory, task_id: str):
    async with session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(ExecutionNode).where(ExecutionNode.task_id == task_id)
        )
        return list(result.scalars().all())


async def _get_node(session_factory, task_id: str, key: str):
    nodes = await _all_nodes(session_factory, task_id)
    return next(n for n in nodes if n.node_key == key)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_tracker.py -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `template.py`**

```python
TASK_GRAPH_NODES: list[dict] = [
    {"node_key": "start", "label": "开始", "kind": "terminal", "sort_order": 0},
    {"node_key": "parse.tender", "label": "招标文件解析", "kind": "file", "sort_order": 10},
    {"node_key": "parse.bid", "label": "标书解析", "kind": "file", "sort_order": 20},
    {"node_key": "index.gate", "label": "等待标书索引", "kind": "gate", "sort_order": 30},
    {"node_key": "index.segments", "label": "索引分段", "kind": "stage", "sort_order": 40},
    {"node_key": "index.enrich", "label": "块增强", "kind": "stage", "sort_order": 50},
    {"node_key": "index.fts", "label": "全文索引", "kind": "stage", "sort_order": 60},
    {"node_key": "index.vectors", "label": "向量索引", "kind": "stage", "sort_order": 70},
    {"node_key": "index.wiki", "label": "Wiki 构建", "kind": "stage", "sort_order": 80},
    {"node_key": "interpret", "label": "招标文件解读", "kind": "stage", "sort_order": 90},
    {"node_key": "checklist.generate", "label": "检查项生成", "kind": "stage", "sort_order": 100},
    {"node_key": "diagnosis", "label": "标书诊断", "kind": "container", "sort_order": 110},
    {"node_key": "report.generate", "label": "报告生成", "kind": "stage", "sort_order": 120},
    {"node_key": "end", "label": "完成", "kind": "terminal", "sort_order": 130},
]

TASK_GRAPH_EDGES: list[dict] = [
    {"from_key": "start", "to_key": "parse.tender", "edge_kind": "sequential"},
    {"from_key": "start", "to_key": "parse.bid", "edge_kind": "parallel"},
    {"from_key": "parse.bid", "to_key": "index.gate", "edge_kind": "sequential"},
    {"from_key": "index.gate", "to_key": "index.segments", "edge_kind": "sequential"},
    {"from_key": "index.segments", "to_key": "index.enrich", "edge_kind": "sequential"},
    {"from_key": "index.enrich", "to_key": "index.fts", "edge_kind": "sequential"},
    {"from_key": "index.fts", "to_key": "index.vectors", "edge_kind": "sequential"},
    {"from_key": "index.vectors", "to_key": "index.wiki", "edge_kind": "sequential"},
    {"from_key": "parse.tender", "to_key": "interpret", "edge_kind": "depends_on"},
    {"from_key": "index.wiki", "to_key": "interpret", "edge_kind": "sequential"},
    {"from_key": "interpret", "to_key": "checklist.generate", "edge_kind": "sequential"},
    {"from_key": "checklist.generate", "to_key": "diagnosis", "edge_kind": "sequential"},
    {"from_key": "diagnosis", "to_key": "report.generate", "edge_kind": "sequential"},
    {"from_key": "report.generate", "to_key": "end", "edge_kind": "sequential"},
]
```

- [ ] **Step 4: 实现 `tracker.py` 核心逻辑**

要点：
- `get_tracker(task_id) -> ExecutionGraphTracker`
- `init_graph()`：幂等（已存在 nodes 则 skip）；bulk insert nodes/edges
- `_start_node(key)`：若已 `running` 则 log warning 并 return False；否则 `status=running`, `started_at=utcnow()`
- `_finish_node(key, status, error=None)`：写 `ended_at`, `duration_ms`, merge meta
- `@asynccontextmanager track(node_key, **kwargs)`：start → yield → complete；except → failed → re-raise
- `track_node(node_key)`：alias of `track`
- `add_node(node_key, parent_key, label, kind, meta)`：insert 新 node + 可选 edge 到 parent
- `notify(node_key, status=None, meta=None)`：merge meta / 更新 status
- 所有 DB 操作包 `try/except`，失败 `logger.warning`，不 raise

- [ ] **Step 5: 添加 `__init__.py`**

```python
from app.services.execution_graph.tracker import ExecutionGraphTracker, get_tracker

__all__ = ["ExecutionGraphTracker", "get_tracker"]
```

- [ ] **Step 6: 运行测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_tracker.py -v`

Expected: PASS（4 tests）

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/execution_graph backend/tests/test_execution_graph_tracker.py
git commit -m "feat: add execution graph tracker with template and tests"
```

---

### Task 3: Graph 查询与 API

**Files:**
- Create: `backend/app/services/execution_graph/query.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/tasks.py`
- Create: `backend/tests/test_execution_graph_api.py`

- [ ] **Step 1: 写失败 API 测试 `backend/tests/test_execution_graph_api.py`**

```python
from __future__ import annotations

import io

import pytest
from sqlalchemy import delete

from app.models import DiagnosisTask, ExecutionEdge, ExecutionNode


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_task(client):
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": (
            "bid.docx",
            io.BytesIO(b"PK fake"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    r = await client.post("/api/tasks", data={"background": "x"}, files=files)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_execution_graph_not_found(client):
    r = await client.get("/api/tasks/T-NOPE/execution-graph")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_execution_graph_after_create(client):
    task_id = await _create_task(client)
    r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task_id
    assert body["legacy"] is False
    keys = {n["key"] for n in body["nodes"]}
    assert "parse.tender" in keys
    assert "diagnosis" in keys
    assert body["summary"]["total_nodes"] >= 10


@pytest.mark.asyncio
async def test_execution_graph_legacy_empty(client, db_session):
    task_id = "T-LEGACY-001"
    async with db_session() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="t.pdf",
                tender_path="/tmp/t.pdf",
                bid_filename="b.docx",
                bid_path="/tmp/b.docx",
                status="completed",
            )
        )
        await session.commit()
    r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["legacy"] is True
    assert body["nodes"] == []
```

注：`test_execution_graph_legacy_empty` 需要 conftest 暴露 `db_session` fixture（与 `test_execution_graph_tracker.py` 相同模式，或复用现有 `client` fixture 的 DB session factory）。

- [ ] **Step 2: 实现 `query.py`**

```python
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped"})

async def build_execution_graph_response(session, task: DiagnosisTask) -> dict:
    # load nodes, edges ordered by sort_order
    # legacy = len(nodes) == 0
    # summary: count by status; total_duration_ms = sum completed duration + running live duration
    # running node: duration_ms = int((utcnow()-started_at).total_seconds()*1000)
    # return dict matching ExecutionGraphOut
```

- [ ] **Step 3: 添加 Pydantic schemas**

```python
class ExecutionNodeOut(BaseModel):
    id: str
    key: str
    label: str
    kind: str
    status: str
    parent_key: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    meta: dict[str, Any] = Field(default_factory=dict)

class ExecutionEdgeOut(BaseModel):
    from_key: str = Field(alias="from")
    to_key: str = Field(alias="to")
    kind: str

class ExecutionGraphSummaryOut(BaseModel):
    total_nodes: int
    completed: int
    running: int
    failed: int
    pending: int
    total_duration_ms: int

class ExecutionGraphOut(BaseModel):
    task_id: str
    task_status: str
    is_terminal: bool
    legacy: bool
    summary: ExecutionGraphSummaryOut
    nodes: list[ExecutionNodeOut]
    edges: list[ExecutionEdgeOut]
```

- [ ] **Step 4: 在 `tasks.py` 添加 endpoint**

```python
@router.get("/{task_id}/execution-graph", response_model=ExecutionGraphOut)
async def get_execution_graph(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return await build_execution_graph_response(db, task)
```

- [ ] **Step 5: 运行 API 测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_api.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/execution_graph/query.py backend/app/schemas.py backend/app/api/tasks.py backend/tests/test_execution_graph_api.py
git commit -m "feat: add execution graph query API"
```

---

### Task 4: 创建任务时 init_graph

**Files:**
- Modify: `backend/app/api/tasks.py`

- [ ] **Step 1: 在 `create_task` 中 `register_task_documents` 之后调用**

```python
from app.services.execution_graph import get_tracker

# after await db.refresh(task) following register_task_documents:
tracker = get_tracker(task_id)
await tracker.init_graph()
```

- [ ] **Step 2: 扩展 API 测试断言 init**

在 `test_execution_graph_after_create` 中 assert `parse.tender` 节点存在且 `status == "pending"`。

- [ ] **Step 3: 运行测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_api.py tests/test_tasks.py -v`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/tasks.py backend/tests/test_execution_graph_api.py
git commit -m "feat: initialize execution graph on task creation"
```

---

### Task 5: parse_scheduler 接入

**Files:**
- Modify: `backend/app/services/parse_scheduler.py`
- Modify: `backend/app/services/parse/pipeline.py`（可选：stage 变更 notify）

- [ ] **Step 1: 在 `_run_job` 中按 file role 选择 node_key**

```python
from app.services.execution_graph import get_tracker

async def _parse_node_key(session, task_id: str, file_id: str) -> str:
    task = await session.get(DiagnosisTask, task_id)
    if task and task.tender_file_id == file_id:
        return "parse.tender"
    if task and task.bid_file_id == file_id:
        return "parse.bid"
    return f"parse.{file_id}"
```

在 `run_parse_pipeline` 外包：

```python
tracker = get_tracker(task_id)
node_key = await _parse_node_key(session, task_id, file_id)
async with tracker.track(node_key):
    result = await run_parse_pipeline(file_id, task_id, stored_path)
```

若 `track` 因已在 running skip，仍执行 pipeline（tracker 内部 guard）。

- [ ] **Step 2: pipeline 内 stage notify（低侵入）**

在 `run_parse_pipeline` 每个 stage 切换处：

```python
from app.services.execution_graph import get_tracker
await get_tracker(task_id).notify(node_key, meta={"parse_stage": "convert"})
```

`node_key` 通过参数传入或在 pipeline 开头由 caller set（推荐参数 `graph_node_key: str | None = None`）。

- [ ] **Step 3: 手动/集成验证**

Run parse 相关现有测试：`cd backend && ../.venv/bin/python -m pytest tests/test_parse_scheduler.py -v`

Expected: PASS（tracker 失败不阻断）

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/parse_scheduler.py backend/app/services/parse/pipeline.py
git commit -m "feat: track parse jobs in execution graph"
```

---

### Task 6: index_scheduler 与 bid_index_wait 接入

**Files:**
- Modify: `backend/app/services/index_scheduler.py`
- Modify: `backend/app/services/bid_index_wait.py`

- [ ] **Step 1: `bid_index_wait.wait_for_bid_index_ready` 包 `index.gate`**

```python
tracker = get_tracker(task_id)
async with tracker.track("index.gate", label="等待标书索引"):
    # existing poll loop
```

- [ ] **Step 2: `index_scheduler._run_job` 分段 track**

仅对 **bid 文件** index job 更新 graph（tender 不索引）。在 `_run_job` 内用嵌套 `async with`：

```python
from app.models import DiagnosisTask
from app.services.execution_graph import get_tracker

async with database.SessionLocal() as session:
    task = await session.get(DiagnosisTask, task_id)
    if task is None or task.bid_file_id != file_id:
        tracker = None
    else:
        tracker = get_tracker(task_id)

if tracker is None:
    await _run_job_original_body(job_id)  # 提取现有 _run_job 逻辑为内部函数，行为不变
else:
    try:
        async with tracker.track("index.segments"):
            segments = materialize_segments(markdown, tree, fine_chunks)
            segments = merge_table_text_into_segments(markdown, tree, segments, table_dir)
        async with tracker.track("index.enrich"):
            catalog = await load_tag_catalog(session)
            segments = await get_chunk_enricher().enrich_many(
                task_id=task_id, segments=segments, catalog=catalog
            )
            await write_segments(session, task_id, file_id, segments, text_dir, document_role=document_role)
            await rebuild_fts_for_file(session, task_id, file_id)
        async with tracker.track("index.vectors"):
            await _rebuild_vectors_for_file(session, task_id, file_id)
        async with tracker.track("index.wiki"):
            await _rebuild_wiki_for_task(session, task_id)
    except Exception as exc:
        await _mark_index_job_failed(job_id, exc)
```

实现时可将现有 `_run_job` 体提取为 `_run_job_original_body`，再在 bid 分支外包 tracker；保持原有 `IndexJob.status/stage` 更新逻辑不变。

- [ ] **Step 3: 运行 index 测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_index_scheduler.py -v`（若存在）

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/index_scheduler.py backend/app/services/bid_index_wait.py
git commit -m "feat: track index pipeline in execution graph"
```

---

### Task 7: scheduler 主流程接入

**Files:**
- Modify: `backend/app/services/scheduler.py`

- [ ] **Step 1: `_run` 中 interpret / checklist 包 track**

```python
async with get_tracker(task_id).track("interpret"):
    interpret_result = await agent.interpret(
        task_id=task_id,
        tender_file_id=tender_file_id,
        background=task.background,
        requirements=task.requirements,
    )

async with get_tracker(task_id).track("checklist.generate"):
    await checklist_service.generate_for_task(task_id)
```

- [ ] **Step 2: `_run_diagnosis_phase` 每个 category 动态节点**

```python
tracker = get_tracker(task_id)
cat_id = category["id"]
node_key = f"diagnosis.category.{cat_id}"
await tracker.add_node(
    node_key=node_key,
    parent_key="diagnosis",
    label=category.get("name") or cat_id,
    kind="batch",
    meta={"category_id": cat_id},
)
async with tracker.track_node(node_key):
    # existing batch diagnosis loop body for this category
```

- [ ] **Step 3: `_complete_from_diagnosis` report 包 track**

```python
async with get_tracker(task_id).track("report.generate"):
    md_path, docx_path = await report.generate_and_save_reports(task_id)
```

成功后将 `start`/`end` 节点按需 mark completed（或在 `_run` 首尾 track start/end）。

- [ ] **Step 4: pause 时 notify diagnosis meta**

```python
await get_tracker(task_id).notify("diagnosis", meta={"paused": True})
```

- [ ] **Step 5: 运行 scheduler 相关测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_scheduler.py tests/test_checklist_api.py -v --ignore=tests/integration`（按仓库实际测试文件调整）

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scheduler.py
git commit -m "feat: track diagnosis scheduler stages in execution graph"
```

---

### Task 8: 重启恢复与任务删除

**Files:**
- Modify: `backend/app/db.py`
- Modify: `backend/app/services/task_delete.py`
- Modify: `backend/tests/test_db.py`

- [ ] **Step 1: `recover_interrupted_tasks` 追加 graph 更新**

```python
from app.models import ExecutionNode

await session.execute(
    update(ExecutionNode)
    .where(ExecutionNode.status == "running")
    .values(status="interrupted")
)
```

- [ ] **Step 2: `task_delete.delete_task` 删除 graph 行**

```python
from app.models import ExecutionEdge, ExecutionNode

await session.execute(delete(ExecutionEdge).where(ExecutionEdge.task_id == task_id))
await session.execute(delete(ExecutionNode).where(ExecutionNode.task_id == task_id))
```

- [ ] **Step 3: 扩展 `test_recover_interrupted_tasks` 或新测试**

插入 running execution node → recover → assert status == `interrupted`。

- [ ] **Step 4: 运行测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_db.py tests/test_task_delete.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/services/task_delete.py backend/tests/test_db.py
git commit -m "feat: recover and delete execution graph rows with tasks"
```

---

### Task 9: 前端依赖与 API

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/api.js`

- [ ] **Step 1: 安装依赖**

Run: `cd frontend && npm install @xyflow/react @dagrejs/dagre`

- [ ] **Step 2: 添加 API helper**

```javascript
export async function getExecutionGraph(taskId) {
  const r = await fetch(`${BASE}/api/tasks/${encodeURIComponent(taskId)}/execution-graph`)
  if (!r.ok) throw new Error(await r.text() || r.statusText)
  return r.json()
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/api.js
git commit -m "feat: add execution graph API client and React Flow deps"
```

---

### Task 10: ExecutionGraph 组件

**Files:**
- Create: `frontend/src/components/execution/ExecutionNodeCard.jsx`
- Create: `frontend/src/components/execution/ExecutionGraph.jsx`

- [ ] **Step 1: `ExecutionNodeCard.jsx`**

自定义 React Flow node：
- props: `data.label`, `data.status`, `data.duration_ms`
- status → CSS class：`node-pending` / `node-running` / `node-completed` / `node-failed` / `node-interrupted`

- [ ] **Step 2: `ExecutionGraph.jsx`**

```javascript
import { useMemo } from 'react'
import { ReactFlow, Background, Controls } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from '@dagrejs/dagre'
import ExecutionNodeCard from './ExecutionNodeCard.jsx'

const nodeTypes = { execution: ExecutionNodeCard }

function layoutWithDagre(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 60 })
  nodes.forEach((n) => g.setNode(n.id, { width: 180, height: 56 }))
  edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return nodes.map((n) => {
    const { x, y } = g.node(n.id)
    return { ...n, position: { x, y } }
  })
}

export default function ExecutionGraph({ graph, selectedKey, onSelectNode }) {
  const { nodes, edges } = useMemo(() => {
    const rfNodes = graph.nodes.map((n) => ({
      id: n.key,
      type: 'execution',
      data: {
        label: n.label,
        status: n.status,
        duration_ms: n.duration_ms,
        selected: n.key === selectedKey,
      },
      position: { x: 0, y: 0 },
    }))
    const rfEdges = graph.edges.map((e, i) => ({
      id: `${e.from}-${e.to}-${i}`,
      source: e.from,
      target: e.to,
    }))
    return { nodes: layoutWithDagre(rfNodes, rfEdges), edges: rfEdges }
  }, [graph, selectedKey])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      onNodeClick={(_, node) => onSelectNode(node.id)}
    >
      <Background />
      <Controls />
    </ReactFlow>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/execution
git commit -m "feat: add execution graph React Flow components"
```

---

### Task 11: TaskProcessPage 与路由

**Files:**
- Create: `frontend/src/pages/TaskProcessPage.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/pages/TaskDetailPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 创建 `TaskProcessPage.jsx`**

- 从 `useParams()` 取 `id`
- 调用 `getExecutionGraph(id)` + 可选 `getTask(id)` 取 `task_status`
- 非终态 → `setInterval` 2s 轮询 graph
- 终态 → 停止轮询
- `legacy`/空 nodes → 展示「暂无进程数据」
- 摘要条：`summary.total_duration_ms`、`completed/total_nodes`、当前 running 节点 label
- 选中节点详情 panel 展示 meta JSON

- [ ] **Step 2: 注册路由**

`App.jsx`:

```jsx
import TaskProcessPage from './pages/TaskProcessPage.jsx'
<Route path="/tasks/:id/process" element={<TaskProcessPage />} />
```

- [ ] **Step 3: TaskDetailPage 添加入口**

在顶栏或状态区增加：

```jsx
<Link to={`/tasks/${id}/process`} className="process-link">查看进程 →</Link>
```

- [ ] **Step 4: CSS**

在 `App.css` 添加：
- `.task-process-page` 布局（图区域 min-height 480px）
- status 颜色变量与 `.execution-node.node-running` pulse 动画
- 详情 panel 样式

- [ ] **Step 5: 手动验证**

Run: `cd frontend && npm run dev`（后端已启动）

1. 创建任务 → 打开 `/tasks/{id}/process`
2. 确认节点随 parse/index 推进变色
3. 终态后轮询停止

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/TaskProcessPage.jsx frontend/src/App.jsx frontend/src/pages/TaskDetailPage.jsx frontend/src/App.css
git commit -m "feat: add task process page with execution graph view"
```

---

### Task 12: 全量回归

- [ ] **Step 1: 后端全量测试**

Run: `cd backend && ../.venv/bin/python -m pytest -q`

Expected: 全部 PASS

- [ ] **Step 2: 前端 lint**

Run: `cd frontend && npm run lint`

Expected: 无 error

- [ ] **Step 3: 可选 — E2E 脚本采集 graph**

在 `scripts/e2e_diagnosis_flow.py` 增加终态采样 `execution-graph.json`（非阻塞，可选）。

- [ ] **Step 4: Commit（若有 E2E 改动）**

```bash
git add scripts/e2e_diagnosis_flow.py
git commit -m "chore: capture execution graph in e2e artifacts"
```

---

## Spec Coverage Self-Review

| Spec 要求 | 对应 Task |
|-----------|-----------|
| execution_nodes / execution_edges 表 | Task 1 |
| ExecutionGraphTracker API | Task 2 |
| init_graph 静态骨架 | Task 2, 4 |
| track / add_node / notify | Task 2, 5–7 |
| GET execution-graph API | Task 3 |
| parse / index / scheduler 接入 | Task 5–7 |
| bid_index_wait index.gate | Task 6 |
| recover interrupted → interrupted | Task 8 |
| task delete 级联 | Task 8 |
| 前端 /tasks/:id/process | Task 11 |
| React Flow + 轮询/终态 | Task 9–11 |
| 老任务 legacy 空图 | Task 3 |
| Tracker 失败不阻断 | Task 2（tracker swallow） |
| 单元 + API 测试 | Task 2–3, 8 |

无 TBD / 占位符遗漏。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-task-execution-graph.md`.

**两种执行方式：**

1. **Subagent-Driven（推荐）** — 每个 Task 派发独立 subagent，Task 间做 review，迭代快  
2. **Inline Execution** — 本会话按 Task 顺序直接实现，批次间 checkpoint Review

你选哪种？
