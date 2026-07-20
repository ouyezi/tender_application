# 标书检索就绪子流程（竖向步骤列表）设计

## 背景与目标

当前任务执行进程图将标书侧链路（标书解析 → 索引分段 → 块增强 → 向量 → Wiki → 等待就绪）与招标侧、诊断侧**平铺**在同一张 DAG 中，导致：

1. 主图节点过多，难以一眼看清任务主线；
2. `index.fts` 未接入 tracker，长期灰色；
3. `index.wiki` 存在状态未正确落库的观测 bug；
4. `index.gate` 与索引子步骤的先后关系在视觉上易误解。

**目标：** 将「标书 → 检索就绪」合并为单个容器节点 `bid.retrieval`，主图只展示顶层节点；点击容器后在右侧详情面板以**竖向步骤列表**展示完整 7 步子流程。首版仅做 `bid.retrieval`，前端组件设计为可复用，下一迭代接入 `diagnosis` 容器。

## 已确认决策

| 决策点 | 选择 |
|--------|------|
| 子流程 UI | 竖向步骤列表（非嵌套 DAG） |
| 首版范围 | 仅 `bid.retrieval`；组件可复用 |
| 全文索引 | 独立步骤 + 补 `track("index.fts")` |
| 等待标书索引就绪 | 作为子流程第 7 步（最后一步） |
| 实现路径 | 后端 `parent_key` + 查询层 rollup（非前端硬编码分组） |

## 非目标

- 首版折叠 `diagnosis` 容器（组件预留复用）
- 主图内嵌 React Flow 子图
- 新增 graph API endpoint 或 WebSocket
- 替换 `DiagnosisTask.status` 等业务状态机

---

## 第一节：节点与边

### 新增容器节点

| node_key | label | kind | parent_key | sort_order |
|----------|-------|------|------------|------------|
| `bid.retrieval` | 标书检索就绪 | `container` | `null` | 25 |

### 子节点（parent_key = `bid.retrieval`）

| sort_order | node_key | label | kind |
|------------|----------|-------|------|
| 26 | `parse.bid` | 标书解析 | `file` |
| 27 | `index.segments` | 索引分段 | `stage` |
| 28 | `index.enrich` | 块增强 | `stage` |
| 29 | `index.fts` | 全文索引 | `stage` |
| 30 | `index.vectors` | 向量索引 | `stage` |
| 31 | `index.wiki` | Wiki 构建 | `stage` |
| 32 | `index.gate` | 等待标书索引就绪 | `gate` |

### 主图顶层节点（parent_key = null）

`start`, `parse.tender`, `bid.retrieval`, `interpret`, `checklist.generate`, `diagnosis`, `report.generate`, `end`，以及运行时 `diagnosis.category.*`（parent_key=`diagnosis`，首版仍可在主图展示）。

### 边调整

**删除（子流程内部 sequential 边改由步骤列表表达）：**

- `start → parse.bid`
- `parse.bid → index.segments`
- `index.segments → index.enrich` … 至 `index.wiki → index.gate`

**新增：**

- `start → bid.retrieval`（`edge_kind=parallel`，与 `start → parse.tender` 并列）

**保留：**

- `start → parse.tender → interpret → checklist.generate`
- `checklist.generate → diagnosis.category.*`（parallel）
- `index.gate → diagnosis.category.*`（depends_on）
- `diagnosis.category.* → report.generate`（parallel）
- `report.generate → end`

**说明：** `bid.retrieval` 不直连 `report.generate`；诊断 category 仍 fan-in 到报告。

---

## 第二节：Tracker 与索引调度

### 子节点 track 接入点（不变或微调）

| 位置 | node_key |
|------|----------|
| `parse_scheduler.py` | `parse.bid` |
| `index_scheduler.py` | `index.segments`, `index.enrich`, **`index.fts`（新增）**, `index.vectors`, `index.wiki` |
| `bid_index_wait.py` | `index.gate` |

### index_scheduler 分段调整

对 bid 文件 index job，`index.enrich` **不再**调用 `rebuild_fts_for_file`；改为独立：

```python
async with tracker.track("index.enrich"):
    # enrich + write_segments + job partial/fts stage meta
async with tracker.track("index.fts"):
    await rebuild_fts_for_file(session, task_id, file_id)
async with tracker.track("index.vectors"):
    ...
async with tracker.track("index.wiki"):
    ...
```

### 父节点 `bid.retrieval`

业务代码**不** `track("bid.retrieval")`。父节点 `status` / `duration_ms` / `started_at` / `ended_at` 由 **query 层从 7 个子节点 rollup**。

### Rollup 规则

**状态优先级（取最差活跃态）：**

`failed` > `running` > `interrupted` > `pending` > `completed`

- 任一子节点 `failed` → 父 `failed`
- 否则任一 `running` → 父 `running`
- 否则任一 `interrupted` → 父 `interrupted`
- 否则任一 `pending` → 父 `pending`
- 全部 `completed` 或 `skipped` → 父 `completed`（全 skipped 时父 `skipped`）

**耗时：**

- `started_at` = 子节点 `started_at` 最小值（忽略 null）
- `ended_at` = 子节点 `ended_at` 最大值（全 completed 时）
- `duration_ms`：父 `running` 时 `now - started_at`；否则 `ended_at - started_at`

### 终态修复（观测层）

任务进入 `completed` / `failed` / `stopped` 时，对 `bid.retrieval` 子树中仍为 `running` 的节点：

- 若对应 `IndexJob.status == ready` 且 key 为 `index.wiki` → 标 `completed`
- 若任务 `completed` 且子节点有 `started_at` 无 `ended_at` → 标 `completed` 并写 `ended_at`

在 `scheduler.py` 任务终态处或 `query.py` 读取时做 sanitize（优先 query 层只读修复，避免改 DB 历史；可选 startup backfill 另议）。

---

## 第三节：API

`GET /api/tasks/{task_id}/execution-graph` **保持 flat 列表**，不新增 endpoint。

每个 node 继续返回 `parent_key`。前端约定：

- **主图：** `nodes.filter(n => !n.parent_key)`
- **步骤列表：** `nodes.filter(n => n.parent_key === 'bid.retrieval').sort(sort_order)`

**summary 调整（首版）：**

- `total_nodes`：只统计 `parent_key == null` 的节点（顶层计数）
- rollup 后的 `bid.retrieval` 计入 `completed` / `running` 等

API 响应中 `bid.retrieval` 节点的 `status` / `duration_ms` 为 rollup 结果（可在 `query.py` 注入虚拟字段或原地更新副本，不写回 DB）。

---

## 第四节：前端

### 主图

`ExecutionGraph` 接收 `visibleNodes` / `visibleEdges`（由 `TaskProcessPage` 过滤）。主图不再渲染 7 个标书索引子节点。

### 新组件 `ExecutionStepList.jsx`

**Props：**

```jsx
ExecutionStepList({ steps, selectedStepKey, onSelectStep })
```

- `steps`: 按 `sort_order` 排序的子节点数组
- 每行：状态圆点 + label + status badge + duration
- 竖向连接线（CSS）
- 点击行：`onSelectStep(key)`，下方展示该步 meta（`parse_stage`, `error` 等）

**复用预留：** `parentKey` 参数化，未来 `diagnosis` 传入 `parent_key="diagnosis"` 即可。

### `TaskProcessPage` 详情面板逻辑

| 选中节点 | 右侧面板 |
|----------|----------|
| 普通顶层节点 | 现有详情（label / status / duration / meta） |
| `bid.retrieval` | 容器 rollup 摘要 + `ExecutionStepList`（7 步） |
| 步骤列表中某一步 | 列表保持 + 该步 meta 块 |

样式：新增 `.process-step-list`、`.process-step-item`、`.process-step-connector`（参考 checklist expand 间距，不复用表格结构）。

### 轮询

不变：非终态 2s 轮询；步骤列表随 graph 数据自动更新。

---

## 第五节：测试

| 测试文件 | 覆盖 |
|----------|------|
| `test_execution_graph_tracker.py` | template 含 `bid.retrieval`；子节点 parent_key；主图边 |
| `test_execution_graph_api.py` | rollup 父 status/duration；summary 顶层计数 |
| `test_index_scheduler.py` | FTS 独立 track；enrich 不含 fts |
| `test_bid_index_wait.py` | gate 仍为子节点，depends_on 边有效 |

---

## 第六节：错误处理

| 场景 | 行为 |
|------|------|
| Tracker 写入失败 | warning log；业务继续（不变） |
| 子节点 failed | 父 rollup 为 failed；步骤列表该行红色 |
| 老任务无 parent_key | 子节点 parent_key 为 null 时仍平铺主图（legacy 兼容：仅新 init_graph 任务有新结构） |
| `index.wiki` stuck running | query 层 sanitize + 终态修复 |

---

## 第七节：后续迭代（不在首版）

- `diagnosis` 容器：主图折叠，`ExecutionStepList` 展示 category batch 步骤
- 可选：`GET .../execution-graph` 增加 `children_by_key` 减少前端过滤
