# 任务执行进程图（Execution Graph）设计

## 背景与目标

当前标书诊断 Demo 的任务执行由三条硬编码流水线驱动（诊断 `scheduler.py`、解析 `parse_scheduler.py`、索引 `index_scheduler.py`），状态分散在 `DiagnosisTask`、`ParseJob`、`IndexJob` 等表，前端仅通过 HTTP 轮询 coarse `status` / `progress`，**没有**统一的 node/edge 模型或可视化入口。

**目标：** 引入可复用的 execution graph 框架，以标准化、低侵入方式接入各执行节点；在任务项下新增「查看进程」页面，展示从开始到完成的全链路 DAG，含节点状态与耗时；分块/batch 子任务（如诊断 category batch）作为动态子节点展示。

## 已确认决策

| 决策点 | 选择 |
|---|---|
| 架构方案 | **方案 1**：中心化 `ExecutionGraphTracker` 服务 |
| 覆盖范围 | 框架优先；首版展示**全链路**（parse / index / interpret / checklist / diagnosis batch / report） |
| 入口 | 任务项下独立页面「查看进程」，**不在首页**展示 |
| 持久化 | **混合模式 B**：`execution_nodes` / `execution_edges` 表；与现有 `DiagnosisTask.status` 等并存，不替代 |
| 接入方式 | **D**：`init_graph` 注册静态骨架 + `track()` context manager + 运行时 `add_node()` |
| 前端更新 | **C**：运行中 2s 轮询 `GET /api/tasks/{id}/execution-graph`；终态后停止轮询，静态读 DB |
| 节点粒度 | **B**：文件/stage 级 + category batch 动态子节点；parse 内部 stage 写入 meta，不单独成节点 |
| 可视化库 | `@xyflow/react` + `@dagrejs/dagre` 自动布局 |

## 非目标

- 替换现有 `DiagnosisTask.status` 状态机或业务决策逻辑
- 首版引入 SSE / WebSocket
- 通用可配置 workflow 引擎（无 YAML/DSL 编排）
- 首页或任务列表嵌入进程图
- parse pipeline 各 stage（convert/extract/chunk）全部展开为独立节点

---

## 第一节：数据模型

### 表 `execution_nodes`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID PK | |
| `task_id` | String FK | 关联 `DiagnosisTask.id` |
| `node_key` | String | 稳定标识，如 `parse.tender`、`index.vectors`、`diagnosis.category.{cat_id}` |
| `parent_key` | String nullable | 父节点 key，如 `diagnosis` |
| `label` | String | 展示名 |
| `kind` | String | `stage` \| `file` \| `batch` \| `gate` \| `terminal` \| `container` |
| `status` | String | `pending` \| `running` \| `completed` \| `failed` \| `skipped` \| `interrupted` |
| `started_at` | DateTime nullable | |
| `ended_at` | DateTime nullable | |
| `duration_ms` | Integer nullable | 完成时写入；running 时 API 层计算 `now - started_at` |
| `meta` | JSON | `file_id`、`category_name`、`error`、`parse_stage` 等 |
| `sort_order` | Integer | 同层排序，默认 0 |
| `created_at` | DateTime | |

**索引：** `(task_id, node_key)` UNIQUE；`(task_id, status)`。

### 表 `execution_edges`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | UUID PK | |
| `task_id` | String | |
| `from_key` | String | 源节点 key |
| `to_key` | String | 目标节点 key |
| `edge_kind` | String | `sequential` \| `parallel` \| `depends_on` |

**索引：** `(task_id, from_key, to_key)` UNIQUE。

### 与现有模型的关系

- `DiagnosisTask.status`、`ParseJob.stage`、`IndexJob.stage` 等**照旧**由业务写入。
- Execution graph 是**观测层**；业务代码不得依赖 graph 表做分支判断。
- Tracker DB 写入失败仅打 warning log，**不阻断**业务执行。

---

## 第二节：Tracker API

模块路径：`backend/app/services/execution_graph/`。

### 核心类 `ExecutionGraphTracker`

```python
tracker = get_tracker(task_id)  # 工厂，便于测试 mock

# 任务创建时（POST /api/tasks）
await tracker.init_graph(TASK_GRAPH_TEMPLATE)

# 执行阶段 — 自动 start/complete/fail + duration
async with tracker.track("interpret", label="招标文件解读", kind="stage"):
    ...

# 动态子节点（诊断 category batch）
node_key = f"diagnosis.category.{cat_id}"
await tracker.add_node(
    node_key=node_key,
    parent_key="diagnosis",
    label=cat_name,
    kind="batch",
    meta={"category_id": cat_id},
)
async with tracker.track_node(node_key):
    ...

# 轻量通知（不需包裹 whole stage）
await tracker.notify("index.vectors", status="running", meta={"progress": "2/5"})
```

### `track()` 语义

1. 进入：`status=running`，`started_at=utcnow()`
2. 正常退出：`status=completed`，写 `ended_at`、`duration_ms`
3. 异常退出：`status=failed`，`meta.error=str(exc)`
4. 节点 key 未 register 时：运行时自动 `add_node`（兜底，优先显式 register）

### `init_graph` 静态模板

任务创建时写入 nodes + edges。首版全链路骨架：

```
start
  ├─ parse.tender ──────────────┐
  ├─ parse.bid ─────────────────┤  edge_kind=parallel
  │                              ↓
  │                         index.gate (等待 bid 索引 ready, kind=gate)
  │                              ↓
  ├─ index.segments → index.enrich → index.fts → index.vectors → index.wiki
  │                              ↓
  ├─ interpret (depends_on parse.tender, edge_kind=depends_on)
  │                              ↓
  ├─ checklist.generate
  │                              ↓
  ├─ diagnosis (kind=container)
  │     └─ [动态] diagnosis.category.{cat_id} × N
  │                              ↓
  └─ report.generate → end
```

**说明：**

- `parse.tender` / `parse.bid`：各对应一个 `WorkspaceFile`；`notify` 更新 `meta.parse_stage`（convert/extract/chunk 等），不单独成节点。
- `index.*` stage 节点在 `init_graph` 预声明；`index_scheduler` 各 stage 用 `track()` 更新。
- `diagnosis.category.*` 在 `_run_diagnosis_phase` 循环内 `add_node` + `track_node()`。

### 服务重启恢复

在 `recover_interrupted_tasks()` 同期，将对应 task 的 `execution_nodes.status=running` 标为 `interrupted`（与业务 `stopped` 语义对齐）。

---

## 第三节：业务接入点

| 位置 | Hook | 说明 |
|------|------|------|
| `api/tasks.py` POST | `tracker.init_graph(task_id)` | 创建任务后立即注册骨架 |
| `parse_scheduler.py` | `track("parse.{tender\|bid}")` | 包裹 `run_parse_pipeline`；stage 变更 `notify(meta.parse_stage=...)` |
| `index_scheduler.py` | `track("index.{stage}")` | segments / enrich / fts / vectors / wiki |
| `scheduler.py` | `track("interpret")` 等 | interpret / checklist.generate / report.generate |
| `scheduler._run_diagnosis_phase` | `add_node` + `track_node` | 每个 category batch |
| `bid_index_wait.py` | `track("index.gate")` | 等待 bid 索引 ready |
| `tender_content.py` | 可选 `notify` | 等待 tender parse 时可更新 gate meta |

**低侵入原则：** 每处 1–3 行；tracker 异常 swallow + log；不 refactor scheduler 主流程。

---

## 第四节：API 契约

### `GET /api/tasks/{task_id}/execution-graph`

**Response 200：**

```json
{
  "task_id": "T-xxx",
  "task_status": "diagnosing",
  "is_terminal": false,
  "legacy": false,
  "summary": {
    "total_nodes": 18,
    "completed": 10,
    "running": 2,
    "failed": 0,
    "pending": 6,
    "total_duration_ms": 125000
  },
  "nodes": [
    {
      "id": "uuid",
      "key": "parse.tender",
      "label": "招标文件解析",
      "kind": "file",
      "status": "completed",
      "parent_key": null,
      "started_at": "2026-07-20T05:00:00Z",
      "ended_at": "2026-07-20T05:02:30Z",
      "duration_ms": 150000,
      "meta": { "file_id": "...", "parse_stage": "chunk" }
    }
  ],
  "edges": [
    { "from": "start", "to": "parse.tender", "kind": "sequential" },
    { "from": "parse.tender", "to": "parse.bid", "kind": "parallel" }
  ]
}
```

**约定：**

- `nodes[].key` 稳定，前端 layout 可缓存 node 位置（可选 localStorage）。
- running 节点：`duration_ms = now - started_at`（API 层实时计算）。
- 任务不存在 → 404。
- 老任务无 graph 记录 → 200，`nodes: []`，`legacy: true`。

**权限：** 与 `GET /api/tasks/{id}` 相同（无额外 admin 限制）。

---

## 第五节：前端「查看进程」页

### 路由

- 路径：`/tasks/:id/process`
- `App.jsx` 注册 `TaskProcessPage`
- `TaskDetailPage` 增加链接「查看进程 →」
- `TaskListPage` / 首页不展示

### 页面结构

1. **顶栏：** 返回任务详情、任务 ID、整体 status badge
2. **摘要条：** 总耗时、已完成 N/M、当前 running 节点名
3. **DAG 图：** React Flow 渲染；横向布局（左 start → 右 end）
4. **节点详情面板：** 选中节点展示 label、status、耗时、meta（error、category、parse_stage）

### 节点视觉

| status | 颜色 |
|--------|------|
| pending | 灰 |
| running | 蓝（可 pulse 动画） |
| completed | 绿 |
| failed | 红 |
| skipped | 浅灰 |
| interrupted | 橙 |

### 轮询

- 读取 `task_status`；非终态（非 completed/failed/stopped）→ 2s 轮询 graph API
- 终态 → 停止轮询，静态展示 DB 数据

### 老任务

`legacy: true` 或 `nodes` 为空时，展示：「暂无进程数据（该任务创建于进程图功能上线前）」。

### 新增文件

| 文件 | 职责 |
|------|------|
| `frontend/src/pages/TaskProcessPage.jsx` | 页面容器、轮询、摘要 |
| `frontend/src/components/execution/ExecutionGraph.jsx` | React Flow + dagre 布局 |
| `frontend/src/components/execution/ExecutionNodeCard.jsx` | 自定义节点组件 |
| `frontend/src/api.js` | `getExecutionGraph(taskId)` |

### 依赖

```json
"@xyflow/react": "^12.x",
"@dagrejs/dagre": "^1.x"
```

---

## 第六节：错误处理

| 场景 | 行为 |
|------|------|
| Tracker DB 写入失败 | warning log；业务继续 |
| 业务 stage 异常 | 对应节点 `failed` + `meta.error` |
| 服务重启 | running → `interrupted`；用户可在图中看到中断节点 |
| pause/resume | `diagnosis` 容器节点 meta 反映 paused；子节点保持最后 status |
| 重复 `track()` 同一 key | 若已在 `running`，log warning 并跳过第二次 start（不 nest）；仍允许 completed 后再次 track（如 retry 场景） |

---

## 第七节：测试

| 层级 | 覆盖 |
|------|------|
| 单元 | `ExecutionGraphTracker`：init_graph、track 正常/异常、add_node、notify、duration 计算 |
| 集成 | 创建任务 → mock 短流程 → GET execution-graph 断言节点数与 status 变迁 |
| API | `test_tasks_api.py` 或新文件：404、legacy 空图、running duration |
| 前端 | ExecutionNodeCard status → 颜色；可选 snapshot |

---

## 方案对比（附录）

| 方案 | 说明 | 未采用原因 |
|------|------|------------|
| 1 中心化 Tracker | 本设计 | **采用** |
| 2 事件溯源 | 仅 Event 表 replay | 首版过重 |
| 3 现有表聚合 | 无新框架 | 不满足标准化接入与 batch 子节点 |

---

## 实现顺序建议

1. DB 模型 + migration + `ExecutionGraphTracker` 单元测试
2. `init_graph` 模板 + `GET execution-graph` API
3. 接入 `parse_scheduler`、`index_scheduler`、`scheduler`（按依赖顺序）
4. 前端 `TaskProcessPage` + React Flow
5. `TaskDetailPage` 入口链接 + E2E 脚本可选采集 graph JSON
