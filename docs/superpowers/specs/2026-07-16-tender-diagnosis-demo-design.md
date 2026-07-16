# 标书诊断 Demo 设计规格

**日期：** 2026-07-16  
**状态：** 已批准  
**范围：** 从零搭建的 Python + 前端 demo，演示标书诊断任务流与管理配置

---

## 1. 目标

构建一个可本地运行的标书诊断演示系统：用户上传招标文件与标书，发起诊断任务；管理端配置诊断项并监控/控制任务进度。诊断引擎默认为可插拔的 Mock 实现，接口预留真实 LLM，不在本 demo 内接入。

### 成功标准

1. 能创建诊断任务（双文件 + 背景/要求），卡片展示文件名、任务 ID、状态、创建时间
2. 任务进入诊断中后，管理页可见进度，支持暂停 / 继续 / 停止
3. 完成后详情页有 Markdown 报告预览 + 诊断项表格，可下载 DOCX 报告
4. 管理页可对诊断配置增删改
5. 存在 `DiagnosisEngine` 接口，默认 `MockEngine`
6. 本地两条命令可分别启动 FastAPI 后端与 Vite 前端

### 明确不做

- 登录鉴权、多用户、权限
- 真实 LLM 调用
- Celery / Redis / 分布式部署
- PDF/DOCX 深度解析与向量检索（Mock 不依赖正文内容）
- 已停止（部分完成）任务的正式报告下载

---

## 2. 技术选型

| 层 | 选型 | 说明 |
|---|---|---|
| 前端 | React + Vite | 单应用多路由 |
| 后端 | FastAPI | REST + 文件上传下载 |
| 存储 | SQLite + 本地目录 | 元数据在库，文件在 `uploads/` / `reports/` |
| 任务执行 | 进程内 asyncio | 轻量单体，无独立 worker |
| 诊断引擎 | `DiagnosisEngine` 协议 | 默认 `MockEngine`，可替换 `LLMEngine` |

**架构路径：** 轻量单体（方案 A）。进程重启时，将仍为 `running` / `paused` 的任务标记为 `stopped`，不恢复内存中的任务。

---

## 3. 页面与路由

同一 React 应用，无鉴权。

| 路由 | 用途 |
|---|---|
| `/` | 诊断任务卡片列表 + 创建诊断 |
| `/tasks/:id` | 任务详情（文件、Markdown 预览、诊断项表、下载） |
| `/admin` | 重定向到 `/admin/configs` |
| `/admin/configs` | 诊断项目配置（增删改） |
| `/admin/tasks` | 诊断任务管理（进度 + 暂停/继续/停止） |

顶部或侧栏提供「诊断页 ↔ 管理后台」互达入口。

---

## 4. 功能规格

### 4.1 诊断任务列表（`/`）

卡片字段：

- 招标文件名、标书文件名
- 任务 ID、状态、创建时间
- 仅 `completed` 显示「下载诊断报告」入口（DOCX）

创建诊断（弹窗或抽屉）：

- 必填：招标文件、标书文件（`.pdf` / `.docx`，单文件上限 2GB）
- 可选：项目背景、诊断要求（多行文本）
- 提交「开始诊断」后创建任务并立即进入 `running`

### 4.2 任务详情（`/tasks/:id`）

自上而下：

1. **文件区：** 招标/标书文件名（可下载原件）、背景与要求摘要、状态；`completed` 时可下载报告
2. **报告预览：** 将报告 Markdown 渲染为可读预览（与 DOCX 同源内容）
3. **诊断项目表：** 列 = 诊断内容、诊断描述、结果、证据、建议

`running` / `paused` 时：展示已产出的部分结果行；报告 Markdown 预览区显示「诊断进行中，报告将在全部完成后生成」。

### 4.3 管理 — 诊断项目配置（`/admin/configs`）

左侧菜单第一项。列表字段：

| 字段 | 说明 |
|---|---|
| 诊断标题 | 短名称 |
| 诊断技巧 | 检查方法 / 给引擎的提示 |
| 诊断内容 | `full_text`（全文，可附范围如目录/正文）或 `description`（内容描述，如「所有资质文件」） |
| 重要性 | `high` / `medium` / `low` |

每行支持编辑、删除；支持新增。

### 4.4 管理 — 诊断任务（`/admin/tasks`）

与前台卡片信息类似，额外：

- **进度：** `progress_done / progress_total` + 进度条
- **控制：**
  - `running` → 暂停、停止
  - `paused` → 继续、停止
  - `completed` / `stopped` / `failed` → 无控制按钮

---

## 5. 数据模型

### 5.1 `diagnosis_configs`

- `id`
- `title`
- `technique`
- `content_mode`：`full_text` | `description`
- `content_scope`：全文时的范围（如 `directory` / `body`），可空
- `content_text`：内容描述文本，可空
- `importance`：`high` | `medium` | `low`
- `created_at` / `updated_at`

### 5.2 `diagnosis_tasks`

- `id`：如 `T-YYYYMMDD-NNN`
- `tender_filename` / `tender_path`
- `bid_filename` / `bid_path`
- `background` / `requirements`
- `status`：见状态机
- `progress_done` / `progress_total`
- `config_snapshot`：创建时配置项 JSON 快照
- `report_md_path` / `report_docx_path`
- `error_message`：失败时可选
- `created_at` / `updated_at` / `finished_at`

创建接口在持久化成功后直接将 `status` 设为 `running` 并调度任务；`pending` 仅作为概念态，正常路径下用户不可见。

### 5.3 `diagnosis_results`

- `id` / `task_id`
- `config_id`：快照来源，可空
- `content_title` / `description` / `result` / `evidence` / `suggestion`
- `sort_order` / `created_at`

**快照规则：** 创建任务时，将当时全部配置项序列化为 JSON，写入 `diagnosis_tasks.config_snapshot`（实现时在任务表增加该字段）。调度器只读该快照，不回查 configs。事后改配置不影响历史任务的总项数与报告。结果行在每项诊断完成时再写入 `diagnosis_results`。

---

## 6. 任务状态机

```
pending ──开始──► running ⇄ paused
                      │
                      ├──全部完成──► completed
                      ├──用户停止──► stopped
                      └──引擎异常──► failed
```

- `paused` 可「继续」回到 `running`
- `stopped` / `completed` / `failed` 为终态；`stopped` 不可继续
- 暂停 / 停止为**协作式检查点**：当前诊断项结束后再生效
- 进度 = 已写入结果条数 / 快照总项数
- `stopped`：保留已有结果，**不提供**正式 DOCX 下载
- `completed`：生成 `report.md` 与 `report.docx` 后才可下载

进程启动恢复：将库中仍为 `running` / `paused` 的任务改为 `stopped`。

---

## 7. 诊断引擎

```text
DiagnosisEngine
  diagnose_item(task, config_snapshot, documents) -> DiagnosisItemResult
```

- **MockEngine：** 按项模拟耗时，返回固定/半随机的结果、证据、建议；不解析文件正文
- **LLMEngine（预留）：** 同接口，后续实现；本 demo 不实现调用逻辑

任务调度器负责：遍历快照项 → 调引擎 → 写 `diagnosis_results` → 更新进度 → 处理暂停/停止标志 → 全部完成后生成报告文件。

报告：Markdown 由结果表汇总生成；DOCX 由同一 Markdown/结构化结果转换（可用 `python-docx`）。

---

## 8. API 轮廓（实现时可微调路径名）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tasks` | 任务列表 |
| POST | `/api/tasks` | 创建并开始（multipart） |
| GET | `/api/tasks/{id}` | 详情（含结果） |
| GET | `/api/tasks/{id}/report.docx` | 下载报告（仅 completed） |
| GET | `/api/tasks/{id}/files/{kind}` | 下载原件 tender/bid |
| POST | `/api/tasks/{id}/pause` | 暂停 |
| POST | `/api/tasks/{id}/resume` | 继续 |
| POST | `/api/tasks/{id}/stop` | 停止 |
| GET/POST | `/api/configs` | 配置列表 / 新增 |
| PUT/DELETE | `/api/configs/{id}` | 更新 / 删除 |

非法状态转换返回 `409`；资源不存在 `404`；校验失败 `400`。

---

## 9. 错误处理

- 上传：非 pdf/docx 或超大小 → 400 + 明确提示
- 创建缺文件 → 400；磁盘失败 → 回滚任务记录
- 引擎异常 → `failed`，保留已有结果，详情展示 `error_message`
- 非法控制操作 → 409；前端按状态禁用按钮
- 非 completed 下载报告 → 404

---

## 10. 目录结构（建议）

```text
tender_application/
  backend/          # FastAPI、SQLite、引擎、任务调度
  frontend/         # React + Vite
  docs/superpowers/specs/
  uploads/
  reports/
  README.md
```

---

## 11. 测试与验收

手动 / 轻量自动化均可，至少覆盖：

1. 配置 CRUD
2. 创建任务 → running → 进度增长 → completed → 可预览与下载
3. 暂停 → 继续 → 完成
4. 停止后不可继续、不可下载正式报告
5. 非法文件类型被拒绝

---

## 12. 决议摘要

| 议题 | 决议 |
|---|---|
| 诊断能力 | 混合：Mock 完整流程 + 可插拔引擎接口 |
| 技术栈 | FastAPI + React (Vite) |
| 鉴权 | 无 |
| 上传格式 | PDF + DOCX |
| 报告下载 | DOCX；详情预览用 Markdown |
| 应用组织 | 单前端，`/` 与 `/admin` |
| 架构 | SQLite + 进程内 asyncio 任务 |
