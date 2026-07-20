# 标书检索就绪子流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将标书侧「解析 → 索引 → 检索就绪」合并为 `bid.retrieval` 容器节点；主图只展示顶层；点击容器后在右侧以竖向步骤列表展示 7 个子步骤；补 `index.fts` track 与父节点 rollup。

**Architecture:** 后端 `template.py` 新增容器并给 7 个子节点设 `parent_key`；`query.py` 对容器做 status/duration rollup；`index_scheduler` 拆分 FTS track；前端 `TaskProcessPage` 过滤主图节点并新增可复用 `ExecutionStepList` 组件。

**Tech Stack:** Python/FastAPI/SQLAlchemy, pytest, React/Vite, @xyflow/react, dagre

**Spec:** `docs/superpowers/specs/2026-07-20-bid-retrieval-subflow-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/services/execution_graph/template.py` | 容器 + parent_key + 主图边 |
| `backend/app/services/execution_graph/query.py` | 父节点 rollup、summary 顶层计数、sanitize |
| `backend/app/services/index_scheduler.py` | FTS 独立 track |
| `backend/tests/test_execution_graph_tracker.py` | template/边/parent_key 测试 |
| `backend/tests/test_execution_graph_api.py` | rollup API 测试 |
| `backend/tests/test_index_scheduler.py` | FTS track 测试 |
| `frontend/src/components/execution/ExecutionStepList.jsx` | 竖向步骤列表（可复用） |
| `frontend/src/pages/TaskProcessPage.jsx` | 主图过滤 + 容器详情 |
| `frontend/src/components/execution/ExecutionGraph.jsx` | 接收 visibleNodes（小改） |
| `frontend/src/App.css` | 步骤列表样式 |

---

### Task 1: 更新 execution graph 模板

**Files:**
- Modify: `backend/app/services/execution_graph/template.py`
- Test: `backend/tests/test_execution_graph_tracker.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_execution_graph_tracker.py` 末尾新增：

```python
@pytest.mark.asyncio
async def test_template_bid_retrieval_container_and_children(db_session):
    from app.services.execution_graph.template import TASK_GRAPH_EDGES, TASK_GRAPH_NODES

    nodes_by_key = {n["node_key"]: n for n in TASK_GRAPH_NODES}
    assert "bid.retrieval" in nodes_by_key
    assert nodes_by_key["bid.retrieval"]["kind"] == "container"

    child_keys = [
        "parse.bid",
        "index.segments",
        "index.enrich",
        "index.fts",
        "index.vectors",
        "index.wiki",
        "index.gate",
    ]
    for key in child_keys:
        assert nodes_by_key[key]["parent_key"] == "bid.retrieval"

    edge_pairs = {(e["from_key"], e["to_key"]) for e in TASK_GRAPH_EDGES}
    assert ("start", "bid.retrieval") in edge_pairs
    assert ("start", "parse.bid") not in edge_pairs
    assert ("parse.bid", "index.segments") not in edge_pairs
    assert ("index.wiki", "index.gate") not in edge_pairs


@pytest.mark.asyncio
async def test_init_graph_sets_bid_retrieval_parent_keys(db_session):
    tracker = ExecutionGraphTracker("T-EG-001")
    await tracker.init_graph()
    bid = await _get_node(db_session, "T-EG-001", "parse.bid")
    assert bid.parent_key == "bid.retrieval"
```

替换 `test_template_wiki_precedes_gate_not_interpret` 中对旧边的断言（删除 `parse.bid → index.segments`、`index.wiki → index.gate` 断言，改为上面新测试覆盖）。

- [ ] **Step 2: 运行测试确认 FAIL**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_tracker.py::test_template_bid_retrieval_container_and_children tests/test_execution_graph_tracker.py::test_init_graph_sets_bid_retrieval_parent_keys -v`

Expected: FAIL（缺少 bid.retrieval / parent_key）

- [ ] **Step 3: 修改 template.py**

```python
TASK_GRAPH_NODES: list[dict] = [
    {"node_key": "start", "label": "开始", "kind": "terminal", "sort_order": 0},
    {"node_key": "parse.tender", "label": "招标文件解析", "kind": "file", "sort_order": 10},
    {"node_key": "bid.retrieval", "label": "标书检索就绪", "kind": "container", "sort_order": 25},
    {"node_key": "parse.bid", "label": "标书解析", "kind": "file", "parent_key": "bid.retrieval", "sort_order": 26},
    {"node_key": "index.segments", "label": "索引分段", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 27},
    {"node_key": "index.enrich", "label": "块增强", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 28},
    {"node_key": "index.fts", "label": "全文索引", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 29},
    {"node_key": "index.vectors", "label": "向量索引", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 30},
    {"node_key": "index.wiki", "label": "Wiki 构建", "kind": "stage", "parent_key": "bid.retrieval", "sort_order": 31},
    {"node_key": "index.gate", "label": "等待标书索引就绪", "kind": "gate", "parent_key": "bid.retrieval", "sort_order": 32},
    # ... interpret, checklist, diagnosis, report, end unchanged sort_order 90+
]

TASK_GRAPH_EDGES: list[dict] = [
    {"from_key": "start", "to_key": "parse.tender", "edge_kind": "sequential"},
    {"from_key": "start", "to_key": "bid.retrieval", "edge_kind": "parallel"},
    {"from_key": "parse.tender", "to_key": "interpret", "edge_kind": "depends_on"},
    {"from_key": "interpret", "to_key": "checklist.generate", "edge_kind": "sequential"},
    {"from_key": "report.generate", "to_key": "end", "edge_kind": "sequential"},
]
```

更新 `tracker.init_graph()` 写入 `parent_key`：

```python
ExecutionNode(
    ...
    parent_key=node_def.get("parent_key"),
    ...
)
```

（若已支持则无需改 tracker。）

- [ ] **Step 4: 运行测试 PASS**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_tracker.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/execution_graph/template.py backend/app/services/execution_graph/tracker.py backend/tests/test_execution_graph_tracker.py
git commit -m "feat: add bid.retrieval container to execution graph template"
```

---

### Task 2: query.py 父节点 rollup

**Files:**
- Modify: `backend/app/services/execution_graph/query.py`
- Test: `backend/tests/test_execution_graph_api.py`

- [ ] **Step 1: 写失败测试**

新建 helper 测试文件或在 `test_execution_graph_api.py` 增加：

```python
@pytest.mark.asyncio
async def test_execution_graph_rollups_bid_retrieval(client, db_session):
    from app.models import ExecutionNode
    from app.services.execution_graph import get_tracker

    task_id = await _create_task(client)
    tracker = get_tracker(task_id)
    async with tracker.track("parse.bid"):
        pass
    async with tracker.track("index.segments"):
        pass
    async with db_session() as session:
        r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    body = r.json()
    container = next(n for n in body["nodes"] if n["key"] == "bid.retrieval")
    assert container["status"] == "running"
    top_level_keys = {n["key"] for n in body["nodes"] if not n.get("parent_key")}
    assert "parse.bid" not in top_level_keys
    assert "index.segments" not in top_level_keys
    assert container["duration_ms"] is not None
```

（根据实际 fixture 调整 `db_session`；若无则直接用 API track 后 GET。）

- [ ] **Step 2: 运行测试 FAIL**

- [ ] **Step 3: 实现 rollup**

在 `query.py` 新增：

```python
BID_RETRIEVAL_KEY = "bid.retrieval"
STATUS_PRIORITY = ("failed", "running", "interrupted", "pending", "completed", "skipped")

def _rollup_container_status(child_statuses: list[str]) -> str:
    for status in STATUS_PRIORITY:
        if status in child_statuses:
            if status == "completed" and all(s in ("completed", "skipped") for s in child_statuses):
                return "completed"
            if status != "completed":
                return status
    return "pending"

def _apply_container_rollups(nodes: list[ExecutionNode], task_status: str) -> list[dict]:
    by_key = {n.node_key: n for n in nodes}
    children_by_parent: dict[str, list[ExecutionNode]] = {}
    for n in nodes:
        if n.parent_key:
            children_by_parent.setdefault(n.parent_key, []).append(n)

    node_out = []
    for node in nodes:
        # build base dict ...
        if node.node_key in children_by_parent:
            children = children_by_parent[node.node_key]
            statuses = [c.status for c in children]
            rolled_status = _rollup_container_status(statuses)
            # compute started_at min, ended_at max, duration
            # override status/duration on container entry
        node_out.append(...)
    return node_out
```

`sanitize`：任务 `task_status in TERMINAL_TASK_STATUSES` 时，子节点 `running` + 有 `started_at` → 输出 `completed`（只影响 API 响应）。

`summary.total_nodes` 改为只计 `parent_key is None` 的节点；`completed/running/...` 用 rollup 后的顶层状态计数。

- [ ] **Step 4: 更新 `test_execution_graph_after_create`**

```python
assert "bid.retrieval" in keys
assert body["summary"]["total_nodes"] < 20  # 顶层节点更少
```

- [ ] **Step 5: 运行测试 PASS**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_api.py tests/test_execution_graph_tracker.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/execution_graph/query.py backend/tests/test_execution_graph_api.py
git commit -m "feat: rollup bid.retrieval container status in execution graph API"
```

---

### Task 3: index_scheduler 拆分 FTS track

**Files:**
- Modify: `backend/app/services/index_scheduler.py`
- Test: `backend/tests/test_index_scheduler.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_bid_index_tracks_fts_as_separate_node(
    db_session, sample_parsed_workspace_file, monkeypatch
):
    from app.models import DiagnosisTask, ExecutionNode
    from sqlalchemy import select

    task = DiagnosisTask(
        id=sample_parsed_workspace_file.task_id,
        tender_filename="t.pdf",
        tender_path="/tmp/t.pdf",
        bid_filename="b.docx",
        bid_path="/tmp/b.docx",
        status="interpreting",
        tender_file_id="tender-id",
        bid_file_id=sample_parsed_workspace_file.id,
    )
    db_session.add(task)
    await db_session.commit()

    from app.services.execution_graph import get_tracker
    await get_tracker(task.id).init_graph()

    await index_scheduler.enqueue(task.id, sample_parsed_workspace_file.id)
    await index_scheduler.drain_once_for_tests()

    fts = (
        await db_session.execute(
            select(ExecutionNode).where(
                ExecutionNode.task_id == task.id,
                ExecutionNode.node_key == "index.fts",
            )
        )
    ).scalar_one()
    assert fts.status == "completed"
```

- [ ] **Step 2: 运行 FAIL**

- [ ] **Step 3: 修改 `_enrich_and_persist` / `_run_job`**

从 `_enrich_and_persist` 移除 `await rebuild_fts_for_file(...)`。

在 bid tracker 分支：

```python
if tracker is not None:
    async with tracker.track("index.enrich"):
        old_text_paths = await _enrich_and_persist_without_fts()
    async with database.SessionLocal() as session:
        async with tracker.track("index.fts"):
            await rebuild_fts_for_file(session, task_id, file_id)
            await session.commit()
    async with database.SessionLocal() as session:
        async with tracker.track("index.vectors"):
            ...
        async with tracker.track("index.wiki"):
            ...
```

非 bid（`tracker is None`）路径保持 enrich 内联 FTS，行为不变。

- [ ] **Step 4: 运行 PASS**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_index_scheduler.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/index_scheduler.py backend/tests/test_index_scheduler.py
git commit -m "feat: track index.fts as separate execution graph stage"
```

---

### Task 4: 前端 ExecutionStepList 组件

**Files:**
- Create: `frontend/src/components/execution/ExecutionStepList.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 创建组件**

```jsx
import { STATUS_LABELS, formatDuration } from './executionFormatters.jsx' // 或内联常量

export default function ExecutionStepList({ steps, selectedStepKey, onSelectStep }) {
  return (
    <ol className="process-step-list">
      {steps.map((step, index) => (
        <li key={step.key} className="process-step-item">
          <button
            type="button"
            className={`process-step-button${selectedStepKey === step.key ? ' is-selected' : ''}`}
            onClick={() => onSelectStep(step.key)}
          >
            <span className={`process-step-dot node-status-${step.status}`} />
            <span className="process-step-label">{step.label}</span>
            <span className={`execution-node-status node-status-${step.status}`}>
              {STATUS_LABELS[step.status] || step.status}
            </span>
            <span className="process-step-duration">{formatDuration(step.duration_ms)}</span>
          </button>
          {index < steps.length - 1 && <span className="process-step-connector" aria-hidden />}
        </li>
      ))}
    </ol>
  )
}
```

可将 `formatDuration` / `STATUS_LABELS` 抽到 `executionFormatters.js` 避免与 `ExecutionNodeCard` 重复。

- [ ] **Step 2: 添加 CSS**

```css
.process-step-list { list-style: none; margin: 0; padding: 0; }
.process-step-item { position: relative; }
.process-step-button {
  width: 100%; display: grid; grid-template-columns: auto 1fr auto auto;
  gap: 8px; align-items: center; text-align: left; padding: 10px 8px;
  border: none; background: transparent; cursor: pointer;
}
.process-step-button.is-selected { background: #f1f5f9; border-radius: 8px; }
.process-step-dot { width: 10px; height: 10px; border-radius: 50%; }
.process-step-connector {
  display: block; width: 2px; height: 12px; background: #cbd5e1; margin-left: 12px;
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/execution/ExecutionStepList.jsx frontend/src/App.css
git commit -m "feat: add reusable ExecutionStepList component"
```

---

### Task 5: TaskProcessPage 主图过滤 + 容器详情

**Files:**
- Modify: `frontend/src/pages/TaskProcessPage.jsx`
- Modify: `frontend/src/components/execution/ExecutionGraph.jsx`

- [ ] **Step 1: 主图过滤**

在 `TaskProcessPage.jsx`：

```jsx
const topLevelNodes = useMemo(
  () => (graph?.nodes ?? []).filter((n) => !n.parent_key),
  [graph],
)

const topLevelKeys = useMemo(
  () => new Set(topLevelNodes.map((n) => n.key)),
  [topLevelNodes],
)

const topLevelEdges = useMemo(
  () =>
    (graph?.edges ?? []).filter(
      (e) => topLevelKeys.has(e.from) && topLevelKeys.has(e.to),
    ),
  [graph, topLevelKeys],
)

const bidRetrievalSteps = useMemo(
  () =>
    (graph?.nodes ?? [])
      .filter((n) => n.parent_key === 'bid.retrieval')
      .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0)),
  [graph],
)
```

注意：API 需返回 `sort_order`（若无则在 schema/query 补上，或用固定 key 顺序 fallback）。

`ExecutionGraph` 改为接收 `nodes` / `edges` props 而非整个 graph，或增加 `visibleNodes` / `visibleEdges`。

- [ ] **Step 2: 详情面板分支**

```jsx
const [selectedStepKey, setSelectedStepKey] = useState(null)

const selectedContainerSteps =
  selectedNode?.key === 'bid.retrieval' ? bidRetrievalSteps : []

const selectedStep =
  selectedStepKey != null
    ? graph?.nodes?.find((n) => n.key === selectedStepKey) ?? null
    : null

// 选中容器时
{selectedNode?.key === 'bid.retrieval' && (
  <>
    {/* rollup 摘要 */}
    <ExecutionStepList
      steps={bidRetrievalSteps}
      selectedStepKey={selectedStepKey}
      onSelectStep={setSelectedStepKey}
    />
    {selectedStep && /* meta block */}
  </>
)}
```

点击主图其他节点时 `setSelectedStepKey(null)`。

- [ ] **Step 3: 手动验证**

Run: `cd /Users/tongqianni/xlab/tender_application && .venv/bin/python startup.py --no-browser`

打开 `/tasks/T-xxx/process`，确认主图无 7 个子节点；点击「标书检索就绪」见 7 步列表。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/TaskProcessPage.jsx frontend/src/components/execution/ExecutionGraph.jsx
git commit -m "feat: collapse bid retrieval subflow into container with step list"
```

---

### Task 6: query 返回 sort_order（若缺失）

**Files:**
- Modify: `backend/app/services/execution_graph/query.py`
- Modify: `backend/app/schemas.py`

- [ ] **Step 1: 在 `ExecutionNodeOut` 增加 `sort_order: int = 0`**

- [ ] **Step 2: `node_out.append` 包含 `sort_order: node.sort_order`**

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas.py backend/app/services/execution_graph/query.py
git commit -m "feat: expose execution node sort_order in graph API"
```

---

### Task 7: 全量回归

- [ ] **Step 1: 后端测试**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_execution_graph_tracker.py tests/test_execution_graph_api.py tests/test_index_scheduler.py tests/test_bid_index_wait.py -v`

Expected: ALL PASS

- [ ] **Step 2: 前端 build**

Run: `cd frontend && npm run build`

Expected: success

- [ ] **Step 3: 最终 commit（若有遗漏）**

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| bid.retrieval 容器 | Task 1 |
| 7 子节点 parent_key | Task 1 |
| 主图边 start→bid.retrieval | Task 1 |
| FTS 独立 track | Task 3 |
| gate 第 7 步 | Task 1（parent_key 已有 track） |
| query rollup | Task 2 |
| summary 顶层计数 | Task 2 |
| ExecutionStepList 可复用 | Task 4 |
| TaskProcessPage 容器详情 | Task 5 |
| diagnosis 不做 | 非目标 |
| sanitize stuck running | Task 2 |
