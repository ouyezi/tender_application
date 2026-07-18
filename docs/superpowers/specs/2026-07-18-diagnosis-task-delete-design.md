# 标书诊断任务删除设计规格

**日期：** 2026-07-18  
**状态：** 已批准  
**范围：** 任务列表页卡片增加三点菜单（查看详情 / 删除）；新增硬删除 API；运行中任务先停止再删除。  
**前置：** [2026-07-16-tender-diagnosis-demo-design.md](./2026-07-16-tender-diagnosis-demo-design.md)

---

## 1. 目标

在首页任务列表卡片上提供删除能力，并配套后端彻底清理任务相关数据与磁盘产物，避免残留占用与孤儿记录。

### 成功标准

1. 列表页每张任务卡片右上角有 `⋯` 菜单，含「查看详情」「删除」
2. 「查看详情」跳转 `/tasks/{id}`（与整卡点击一致）
3. 「删除」先二次确认，确认后调用删除 API，列表刷新且该任务消失
4. 任意状态（含解读中 / 生成检查项 / 诊断中）均可删除；运行中先 stop 再删
5. 删除后 DB 关联行与 `uploads/{task_id}/` 等磁盘产物被清理
6. 菜单点击不触发卡片导航

### 明确不做（本期）

- 管理端任务表、详情页的删除入口
- 软删除 / 回收站 / 撤销
- 引入 UI 组件库
- 批量删除

---

## 2. 方案选择

采用**轻量三点菜单 + 硬删除 API**（方案 A）：

| 方案 | 说明 | 结论 |
|---|---|---|
| A. 原生下拉 + `DELETE` 硬删 | 与现有 `ConfigsPage` 确认模式一致，改动小 | **采用** |
| B. 引入 Dropdown 组件库 | 仅为两个菜单项引入依赖 | 过重 |
| C. 软删除 | 实现快但磁盘与关联数据残留 | 不符合「删除」预期 |

---

## 3. 交互设计

### 3.1 卡片菜单

- 位置：`TaskCard` 右上角（header 区域）
- 触发：点击 `⋯` 切换下拉显隐；点击外部或选择项后关闭
- 菜单项顺序：
  1. **查看详情**
  2. **删除**（可用危险色区分）
- `⋯`、菜单容器、菜单项均 `stopPropagation`，避免触发卡片 `onClick`
- 「查看详情」调用与整卡相同的导航逻辑

### 3.2 删除确认

- 使用 `window.confirm`，文案：`确定删除该诊断任务？此操作不可恢复。`
- 取消：无操作
- 确认：调用 `deleteTask(id)`；成功后刷新列表；失败在列表页顶部展示错误

### 3.3 加载与禁用

- 删除请求进行中：可禁用该卡片菜单或整页短暂 loading，防止重复提交
- 不强制全局遮罩

---

## 4. API 设计

### `DELETE /api/tasks/{task_id}`

| 项 | 值 |
|---|---|
| 成功 | `204 No Content` |
| 不存在 | `404`，`detail: Task not found` |
| 其它失败 | `500` 或业务错误信息（尽量保证 stop 后仍能尽力清理） |

**处理流程：**

1. 加载任务；不存在则 404
2. 若任务在诊断 scheduler 中处于可停止状态，调用现有 stop 逻辑（忽略「已非运行态」类冲突，保证幂等）
3. 执行级联清理（见 §5）
4. 提交事务；返回 204

前端：`api.js` 新增 `deleteTask(id)` → `DELETE /api/tasks/{id}`。

---

## 5. 数据清理

### 5.1 数据库（建议顺序）

因存在 `DiagnosisTask.current_checklist_generation_id → checklist_generations.id` 外键，需先解除自引用：

1. 将任务的 `current_checklist_generation_id` 置 `NULL`
2. 删除 `diagnosis_results`（按 `task_id`；含 cascade 关系亦可依赖 ORM）
3. 按 generation 删除 checklist：`checklist_items` → `checklist_categories` → `checklist_generations`（`task_id`）
4. 删除 `parse_jobs`、`index_jobs`（`task_id`）
5. 删除 `knowledge_chunks`、`wiki_pages`（`task_id`）
6. 删除 `workspace_files`（`task_id`）
7. 删除 `diagnosis_tasks` 行

说明：`ParseJob` / `IndexJob` / `KnowledgeChunk` / `WikiPage` 部分字段无完整 ORM cascade，必须显式删除。

### 5.2 磁盘

1. 删除 Artifact 根目录：`uploads/{task_id}/`（`shutil.rmtree`，不存在则忽略）
2. 若 `report_*` / `interpret_*` 路径指向该目录外的文件（如历史 `reports/`），一并删除存在的文件
3. 磁盘清理失败应记录日志；是否回滚 DB 由实现选择——**推荐：DB 成功优先，磁盘失败记 warning 不阻断**（避免因文件锁导致无法删任务），并在实现计划中写清

### 5.3 调度器

- 删除前尽量 stop，清理 in-memory control 状态，避免后台协程继续写已删任务

---

## 6. 前端改动文件

| 文件 | 改动 |
|---|---|
| `frontend/src/api.js` | `deleteTask(id)` |
| `frontend/src/components/TaskCard.jsx` | 三点菜单；`onViewDetail` / `onDelete` |
| `frontend/src/pages/TaskListPage.jsx` | confirm + delete + refresh |
| `frontend/src/App.css` | `.task-card-menu` 等样式 |

管理端与详情页本期不改。

---

## 7. 测试要点

1. 已完成任务：删除后列表无该项；`GET /api/tasks/{id}` 404；`uploads/{id}` 不存在
2. 运行中任务：删除成功且 scheduler 不再推进该任务
3. 含 checklist / workspace / index 关联的任务：关联表无残留
4. 前端：菜单项不触发卡片导航；取消确认不发请求
5. 删除不存在的 id → 404

---

## 8. 决策记录

| 决策 | 选择 |
|---|---|
| 确认方式 | `window.confirm` |
| 运行中任务 | 允许，先 stop 再删 |
| 覆盖页面 | 仅首页列表卡片 |
| 删除语义 | 硬删除（DB + 磁盘） |
| UI | 原生三点下拉，不引组件库 |
