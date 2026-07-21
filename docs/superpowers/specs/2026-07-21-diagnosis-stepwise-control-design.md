# 诊断分步控制设计规格

**日期：** 2026-07-21  
**状态：** 待审阅  
**范围：** 诊断任务创建改为草稿模式；任务详情页增加分步操作按钮（生成诊断项 / 标书索引 / 诊断 / 一键诊断 / 暂停继续）

---

## 1. 目标

将当前「创建即全自动跑完整流水线」改为「创建草稿 → 用户手动分步或一键执行」，支持：

1. 创建诊断时不自动开始任何流程，任务处于 `draft` 状态
2. 任务详情文件区增加操作按钮区，可独立触发各步骤
3. 「生成诊断项」与「标书索引」可并行执行
4. 「诊断」需在前两步（按实际需要）完成后才可执行
5. 「一键诊断」智能续跑，跳过已完成步骤
6. 「暂停/继续」在所有运行中流程（分步与一键）均可用，从前台详情页操作

### 成功标准

1. 创建任务后状态为 `draft`，不 enqueue ParseJob，不启动 scheduler
2. 详情页五个按钮按规则启用/禁用，运行中显示 loading
3. 分步执行与一键执行共用后端步骤函数，行为与现有 execution graph 一致
4. 并行触发「生成诊断项」和「标书索引」互不阻塞（409 仅在同 lane 重复触发时返回）
5. pause/resume 覆盖 interpreting / generating_checklist / indexing_bid / diagnosing 阶段
6. 现有 E2E 一键路径仍可用（通过 `run-full` action）

### 明确不做（本期）

- 步骤成功后强制重跑
- 详情页「停止」按钮（管理后台保留）
- 修改 parse/index worker 内部 pause 逻辑
- 登录鉴权

---

## 2. 背景与现状

### 当前行为

- `POST /api/tasks` 创建后立即：`register_task_documents`（enqueue 招标+标书 parse）→ `init_graph()` → `parse_scheduler.kick()` → `scheduler.start_task()`
- 主链路：interpreting → generating_checklist → diagnosing → completed
- 标书索引（parse.bid → index.* → index.gate）与 checklist 路径并行，由 execution graph 展示
- pause/resume API 已有，但仅 `diagnosing` 可暂停；前端仅在 `/admin/tasks` 暴露

### 关键文件

| 层 | 路径 |
|---|---|
| 创建弹窗 | `frontend/src/components/CreateTaskModal.jsx` |
| 任务详情 | `frontend/src/pages/TaskDetailPage.jsx` |
| 任务 API | `backend/app/api/tasks.py` |
| 主调度器 | `backend/app/services/scheduler.py` |
| 解析 worker | `backend/app/services/parse_scheduler.py` |
| 索引 worker | `backend/app/services/index_scheduler.py` |
| 工作区注册 | `backend/app/services/workspace.py` |

---

## 3. 需求决策摘要

| 决策点 | 选择 |
|--------|------|
| 创建后状态 | 草稿 `draft`，不自动执行任何步骤 |
| 生成诊断项 | 招标解析 → 解读 → 生成检查项（不含标书解析/索引） |
| 标书索引 | 标书解析 → 完整索引链（与 execution graph `bid.retrieval` 一致） |
| 分步并行 | 「生成诊断项」与「标书索引」互不阻塞 |
| 暂停/继续 | 所有运行中流程均可用（分步与一键） |
| 一键诊断 | 智能续跑，跳过已完成步骤 |
| 重跑策略 | 步骤已成功则按钮禁用；`failed`/`stopped` 时重新可用 |

---

## 4. 方案选型

### 方案 A：分步 Action API + 调度器 Lane 拆分（选用）

新增 action 端点，将 `scheduler._run` 拆成可组合步骤；标书索引复用 parse/index worker。

- 优点：改动集中、execution graph 基本不动、分步与一键共用步骤函数
- 缺点：需扩展 pause/resume；并行时需协调多 lane 状态展示

### 方案 B：run_mode + 单一编排器

新增 `run_mode` 和 `completed_steps` JSON，统一 orchestrator 调度。

- 优点：状态模型清晰
- 缺点：与现有单 `bg_task` 冲突，重构量大

### 方案 C：仅前端分步，后端仍 start_task

- 优点：API 改动最小
- 缺点：无法保证「生成诊断项」不触发标书侧；语义不清

**选用方案 A。**

---

## 5. 架构与状态模型

### 5.1 任务生命周期

```text
创建（draft）
  ├─ [生成诊断项] → interpreting → generating_checklist → checklist_ready*
  ├─ [标书索引]   → indexing_bid → bid_index_ready*
  ├─ [诊断]       → diagnosing → completed（需 * 就绪）
  └─ [一键诊断]   → 智能续跑上述全部 → completed

任意运行中 → [暂停] ↔ [继续]
```

`*` 不作为 DB 状态，由现有字段推导：

- `checklist_ready` = `current_checklist_generation_id != null`
- `bid_index_ready` = 对应 `IndexJob.status == ready`（检查项全为 `offline` 时可跳过）

### 5.2 状态字段

| 状态 | 含义 |
|------|------|
| `draft` | 刚创建，未执行任何步骤 |
| `indexing_bid` | 标书索引 lane 运行中（解析或索引阶段） |
| `interpreting` | checklist lane：等待招标 markdown / 解读中 |
| `generating_checklist` | checklist lane：生成检查项 |
| `diagnosing` | 诊断阶段 |
| `paused` | 已暂停（可从上述运行态进入） |
| `completed` / `failed` / `stopped` | 终态（沿用现有） |

**并行时主状态：** 取优先级最高的活跃 lane（`generating_checklist` > `indexing_bid` > `interpreting`）；详情页步骤状态条与 execution graph 展示细粒度进度。

### 5.3 Lane 划分

| Lane | 执行载体 | 步骤 |
|------|----------|------|
| Checklist | `scheduler.bg_task` | 招标 parse（enqueue + wait）→ interpret → checklist.generate |
| Bid index | `parse_scheduler` + `index_scheduler` | 标书 parse → index.* → index.gate |
| Diagnosis | `scheduler.bg_task` | wait index.gate（如需）→ diagnosis.* → report.generate |
| Full | `scheduler.bg_task` | 智能续跑上述全部 |

共享 `_TaskControl.pause_event` / `stop_requested`；`_wait_if_paused` 嵌入各 lane 等待点（含 `wait_for_tender_parse_ready`、`wait_for_bid_index_ready` 轮询）。

---

## 6. 后端 API

### 6.1 创建任务（变更）

`POST /api/tasks`

- 保存文件、快照 config、`register_task_documents(enqueue_parse=False)`
- `status = "draft"`
- `init_graph()`（节点全 pending）
- **不**调用 `parse_scheduler.kick()` 或 `scheduler.start_task()`

### 6.2 新增 Action 端点

均返回 `202 Accepted` + `{ task_id, status }`。

| 端点 | 行为 |
|------|------|
| `POST /api/tasks/{id}/actions/generate-checklist` | enqueue 招标 parse（若未成功）→ kick parse → 跑 interpret + checklist，完成后停在 checklist_ready |
| `POST /api/tasks/{id}/actions/index-bid` | enqueue 标书 parse（若未成功）→ kick parse → 等待 index ready，完成后停在 bid_index_ready |
| `POST /api/tasks/{id}/actions/diagnose` | 校验前置 → 跑诊断 + 报告生成 |
| `POST /api/tasks/{id}/actions/run-full` | 智能续跑：缺什么补什么（可并行 checklist + bid index lane），最后诊断 |

### 6.3 就绪查询（可选）

`GET /api/tasks/{id}/readiness`

```json
{
  "checklist_ready": true,
  "bid_index_ready": false,
  "bid_index_required": true,
  "checklist_lane_active": false,
  "bid_index_lane_active": true,
  "diagnosis_ready": false
}
```

也可扩展 `TaskOut` 嵌入同结构，避免额外请求。

### 6.4 暂停/继续（扩展）

- `POST /api/tasks/{id}/pause`：运行中（`interpreting` / `generating_checklist` / `indexing_bid` / `diagnosing`）→ `paused`
- `POST /api/tasks/{id}/resume`：`paused` → 恢复之前阶段，重启对应 runner（smart resume）

### 6.5 冲突与错误码

| 场景 | HTTP | detail |
|------|------|--------|
| 同 lane 已在运行 | 409 | `task_lane_active` |
| 诊断时 checklist 未就绪 | 409 | `checklist_not_ready` |
| 诊断时有 file 项但 index 未 ready | 409 | `bid_index_not_ready` |
| 非法状态触发 action | 409 | `invalid_task_status` |
| 步骤已成功不可重跑 | 409 | `step_already_completed` |

失败仍写 `failed` + `failure_stage` + `error_message`。bid index 失败新增 `failure_stage`: `bid_index`。

---

## 7. 后端实现要点

### 7.1 workspace.py

```python
async def register_task_documents(..., *, enqueue_parse: bool = True) -> ...
```

`enqueue_parse=False` 时仅创建 `WorkspaceFile`，不创建 `ParseJob`。

### 7.2 scheduler.py 拆分

从现有 `_run` 提取：

- `_run_checklist_lane(task_id)` — interpret + checklist，不含 diagnosis
- `_run_bid_index_lane(task_id)` — 触发 bid parse、更新 `indexing_bid` 状态、等待 index ready
- `_run_diagnosis_lane(task_id)` — 现有 `_complete_from_diagnosis` / `_run_diagnosis_phase`
- `_run_full(task_id)` — 智能续跑 orchestrator

各 action 入口检查 lane 活跃状态，spawn 对应 asyncio task。

`_run` 保留为 `_run_full` 的内部实现或 deprecated 别名，供 `run-full` 使用。

### 7.3 smart resume 逻辑（run-full）

```text
if not interpret_md_path and not checklist_ready:
    start checklist lane (async)
if bid_index_required and not bid_index_ready:
    start bid index lane (async)
await both lanes if started
if not checklist_ready or (bid_index_required and not bid_index_ready):
    fail or wait
await diagnosis lane
```

### 7.4 进程重启

沿用现有策略：非终态 `running`/`paused`/`interpreting` 等 → `stopped`；`draft` 保持不变。

---

## 8. 前端设计

### 8.1 创建入口

**CreateTaskModal**

- 提交按钮：`开始诊断` → `创建`
- 提交成功后跳转 `/tasks/{id}`

**TaskListPage / TaskCard**

- 新增 `draft` 徽章：**待执行**

### 8.2 详情页按钮区

位于「文件」区块内，文件列表下方：

```text
[生成诊断项] [标书索引] [诊断]  |  [一键诊断]  [暂停/继续]
```

上方步骤状态条：

```text
● 诊断项    ○ 标书索引    ○ 诊断
  已生成      索引中        待执行
```

### 8.3 按钮启用规则

| 按钮 | 可用条件 |
|------|----------|
| 生成诊断项 | 非终态；checklist 未就绪或 failed/stopped；checklist lane 未运行 |
| 标书索引 | 非终态；index 未 ready 或 failed/stopped；bid index lane 未运行 |
| 诊断 | checklist_ready 且 (bid_index_ready 或 无需 index)；非 completed；diagnosis lane 未运行 |
| 一键诊断 | 非终态；full run 未运行 |
| 暂停/继续 | 运行中或 paused |

运行中按钮显示 loading 文案（生成中… / 索引中… / 诊断中… / 执行中…）。

### 8.4 API 封装（api.js）

```javascript
generateChecklist(taskId)
indexBid(taskId)
runDiagnosis(taskId)
runFullDiagnosis(taskId)
getTaskReadiness(taskId)  // 若独立端点
```

轮询扩展：`POLL_STATUSES` 加入 `draft`（仅当有 lane active 时轮询）、`indexing_bid`。

### 8.5 与管理后台关系

管理后台 `/admin/tasks` 保留 pause/resume/stop，与详情页共用 API。

---

## 9. 边界情况

| 场景 | 处理 |
|------|------|
| 检查项全为 `offline` | 「诊断」不要求 bid index；跳过 `index.gate` |
| 一键诊断中途暂停 | resume 从暂停点继续 |
| checklist 完成但 index 未完成 | 「诊断」禁用；「标书索引」仍可用 |
| 并行触发两 action | 均 202；parse worker 全局串行 dequeue，但两 lane 状态独立 |
| 步骤已成功 | 对应按钮禁用，避免误覆盖 |

---

## 10. 测试计划

### 后端

- 创建 → `draft`，无 ParseJob
- `generate-checklist` → interpret + checklist，不触发 bid index
- `index-bid` → 仅 bid parse + index
- 并行两 action 均 202
- `diagnose` 前置 409 校验
- `run-full` smart resume
- pause/resume 在 generating_checklist / indexing_bid 阶段

### 前端

- draft 初始按钮状态
- 步骤完成后启用/禁用切换
- 暂停/继续 UI

### E2E（可选）

- 扩展 `scripts/e2e_diagnosis_flow.py`：分步 + 一键路径

---

## 11. 改动文件清单

| 文件 | 改动 |
|------|------|
| `backend/app/models.py` | 文档化新状态（无 schema 迁移） |
| `backend/app/schemas.py` | `TaskReadinessOut`、扩展 `TaskOut` |
| `backend/app/api/tasks.py` | draft 创建 + action 端点 |
| `backend/app/services/workspace.py` | `enqueue_parse` 参数 |
| `backend/app/services/scheduler.py` | lane 拆分 + pause 扩展 |
| `backend/tests/test_scheduler.py` | lane / pause 测试 |
| `backend/tests/test_tasks_api.py` | action 端点测试 |
| `frontend/src/components/CreateTaskModal.jsx` | 文案 + 跳转 |
| `frontend/src/pages/TaskListPage.jsx` | draft 徽章 |
| `frontend/src/components/TaskCard.jsx` | draft 状态 |
| `frontend/src/pages/TaskDetailPage.jsx` | 按钮区 + 状态条 |
| `frontend/src/api.js` | 新 API |
