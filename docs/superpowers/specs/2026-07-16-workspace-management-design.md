# 工作区管理（Artifact 文件与文档解析）设计规格

**日期：** 2026-07-16  
**状态：** 待用户确认  
**范围：** 新增工作区管理页；按 Artifact（= 任务 ID）组织上传文件与解析产物；异步文档处理（markdown / 图片 / 表格 / 文档树 / 分块）；章节树浏览。本期不做检索。  
**前置：** [2026-07-16-tender-diagnosis-demo-design.md](./2026-07-16-tender-diagnosis-demo-design.md)  
**相关：** [2026-07-16-tender-interpretation-report-design.md](./2026-07-16-tender-interpretation-report-design.md)

---

## 1. 目标

在现有标书诊断 Demo 上增加**工作区管理**能力：以诊断任务 ID 为 Artifact，统一管理招标文件、标书及任务相关导入文件；导入后异步解析，产出可浏览的文档树与章节内容，并为后续向量/关键字检索预留分块。

### 成功标准

1. 存在独立页面 `/workspaces` 与 `/workspaces/:taskId`，可从导航进入
2. Artifact 目录结构符合约定（`document` / `markdown` / `image` / `table` / `json` / `report` / `other` / `index.md`）
3. 创建诊断任务时，招标文件与标书自动入库并各启动一条解析任务
4. 工作区可按**自由标签**导入文件；`pdf`/`docx` 自动解析，其它进 `other/`
5. 解析产出：markdown、按源文件区分的图片、高质量表格（含合并单元格的 HTML）、文档树 JSON、分块 JSON、更新后的 `index.md`
6. 详情页：上方文件列表；选中文件后下方左侧文档树、右侧章节内容
7. 文档树识别准确：融合 TOC 与标题/序号信号，且**目录区不参与章节切分**
8. 表格抽取失败可告警、`partial` 状态、支持重试；转换/树构建致命失败为 `failed` 并可重试

### 明确不做（本期）

- 关键字检索、向量检索 / embedding
- 登录鉴权、多用户
- Celery / Redis / 分布式 worker
- 外部文档智能 API
- 扫描件 OCR 全覆盖
- 解读/诊断引擎依赖解析结果（本期仍可 Mock，诊断不等待解析完成）

---

## 2. 方案选择

采用**独立工作区子系统 + 分阶段本地解析管线**（方案 B）：

- Artifact ID = 诊断任务 ID（如 `T-20260716-001`）
- 新增 `WorkspaceFile` / `ParseJob` 与 Parse scheduler，与诊断调度并行、互不阻塞
- 管线阶段：`convert → extract → build_tree → chunk → write_index`
- 转换与树构建可插拔，便于单测与后续替换实现

不采用：把逻辑塞进现有 `files.py`/诊断 scheduler 的最小改动；也不采用外部文档 API。

---

## 3. 架构

| 组件 | 职责 |
|---|---|
| Workspace API | 列表/详情、上传、解析状态、文件树、章节内容、重试、原件下载 |
| Workspace store | SQLite 元数据 + 磁盘 Artifact 目录 |
| Parse pipeline | 分阶段文档处理 |
| Parse scheduler | 进程内 asyncio 队列，与诊断 scheduler 并列 |
| Workspace UI | `/workspaces` 列表 + `/workspaces/:taskId` 详情 |

**与现有系统边界：**

- 创建任务流程不变；落盘招标/标书后额外入队两条 ParseJob（默认标签「招标文件」「标书」）
- 解读/诊断本期不依赖解析完成
- 正式报告仍按现有 `reports/` 逻辑生成；同时同步/拷贝到 Artifact 的 `report/`，便于统一索引

**进程重启：** 仍为 `running` 的 ParseJob **重新 `queued`**（解析幂等，覆盖同 `file_id` 产物）。诊断任务的中断恢复策略保持现有设计不变。

---

## 4. 目录结构与数据模型

### 4.1 磁盘布局

根目录：`uploads/{task_id}/`

```text
{task_id}/
├── document/                 # 原始 Word/PDF
│   └── {file_id}_{safe_name}.ext
├── markdown/
│   └── {file_id}.md
├── image/
│   └── {file_id}/
│       └── img_001.png
├── table/
│   └── {file_id}/
│       ├── tbl_001.html      # 保留合并单元格（rowspan/colspan）
│       └── tbl_001.csv       # 能展平则写
├── json/
│   ├── {file_id}.tree.json
│   ├── {file_id}.chunks.json
│   └── {file_id}.meta.json
├── report/                   # 解读/诊断报告同步
├── other/                    # 不走解析管线的附件
└── index.md
```

**兼容迁移：** 创建任务若仍短暂写入根下 `tender.*` / `bid.*`，入库后迁入 `document/`，并更新 `DiagnosisTask.tender_path` / `bid_path`，避免双份真相。

### 4.2 `index.md`

人可读索引：每个文件的标签、原始名、`file_id`、路径、解析状态、主要产物路径、告警摘要。导入或解析状态变更时重写。

### 4.3 WorkspaceFile

| 字段 | 说明 |
|---|---|
| id | 稳定 `file_id`（短 UUID） |
| task_id | Artifact = 任务 ID |
| label | 自由标签（创建时默认「招标文件」「标书」） |
| original_filename | 原始文件名 |
| stored_path | `document/` 或 `other/` 下路径 |
| kind | `document` \| `other` |
| content_type / ext | 如 pdf、docx |
| parse_status | `pending` \| `running` \| `succeeded` \| `failed` \| `partial` \| `skipped`（`kind=other` 不解析） |
| parse_error | 失败摘要（可空） |
| tree_path / md_path / chunks_path | 产物路径（可空） |
| created_at / updated_at | |

`DiagnosisTask` 增加可选字段 `tender_file_id` / `bid_file_id`，关联对应 WorkspaceFile，便于任务详情跳转工作区。

### 4.4 ParseJob

| 字段 | 说明 |
|---|---|
| id | |
| file_id / task_id | |
| status | `queued` \| `running` \| `succeeded` \| `failed` |
| stage | `convert` \| `extract` \| `build_tree` \| `chunk` \| `write_index` |
| attempt | 重试次数 |
| error_message | |
| warnings | 文本或 JSON |
| created_at / started_at / finished_at | |

### 4.5 文档树 JSON

每个节点至少包含：

- `id`、`title`、`level`、`numbering`（如 `1.2.3`）、`parent_id`
- `start_offset` / `end_offset`：本节正文起止（markdown 字符偏移）
- `self_start` / `subtree_end`：含自身到整棵子树结束（到下一同级标题前）
- `source`：`toc` \| `heading` \| `numbering`（或组合标记）

章节正文通过 markdown 文件 + offset 切片读取，不为每章单独落文件。

---

## 5. 解析管线

```text
queued → convert → extract → build_tree → chunk → write_index → succeeded
                              ↘ partial（表格等非致命失败，其余完成）
                              ↘ failed（转换或树构建等致命失败）
```

### 5.1 convert

- 输入：`document/` 下 pdf/docx
- 输出：`markdown/{file_id}.md`
- DOCX：结构化读取（样式/大纲级别）优先
- PDF：页面文本 + 字体/字号等启发式标题，再与 TOC 对齐
- 图片可在本阶段或 extract 阶段抽出

### 5.2 extract

**图片：** 按源文件写入 `image/{file_id}/`；markdown 内链接改为相对路径。

**表格（高质量，本期要求）：**

- DOCX：读原生 table（含 `gridSpan` / `vMerge`）→ HTML（保留 rowspan/colspan）+ 尽力 CSV
- PDF：版面分析抽取；合并单元格以 HTML 为准
- 单表失败 → warning，该表跳过，整篇可标 `partial`；支持整文件重试
- markdown 中用占位引用（如 `<!-- table:tbl_001 -->`）指向 `table/{file_id}/`

### 5.3 build_tree（准确度核心）

多信号融合，避免目录页破坏正文切分：

1. **TOC 区识别：** 文首目录块（「目录」标题、页码点线、TOC 域等）标为 `is_toc_region`，**不参与章节切分**，只作标题/序号词典
2. **标题候选：** markdown `#` 层级 + DOCX 大纲/样式 + 序号模式（`第X章`、`1.`、`1.1`、`（一）` 等）
3. **序号对齐：** 用 TOC 条目校验正文标题顺序与层级；冲突时优先「正文标题 + 序号连续性」，TOC 仅纠偏层级
4. **范围：** 写入本节 `start/end` 与含子孙的 `self_start/subtree_end`
5. **退化：** 无有效标题时生成「全文」单节点树 + warning，仍继续 chunk；最终状态为 `succeeded`（仅带 warnings）。若同时存在表格抽取失败，则为 `partial`

输出：`json/{file_id}.tree.json`

### 5.4 chunk

- 默认按叶子章节切片；超长节再按段落窗口切分
- 每块：`chunk_id`、`node_id`、`title_path`、`start`/`end`、`text`
- 输出：`json/{file_id}.chunks.json`
- **本期不实现检索 API**，只保证块与树可追溯

### 5.5 write_index

重写 Artifact 根 `index.md`；写 `json/{file_id}.meta.json`（warnings、表数量、树节点数等）。

### 5.6 触发

| 事件 | 行为 |
|---|---|
| 创建诊断任务 | 招标 + 标书各建 WorkspaceFile + ParseJob（label 默认「招标文件」「标书」） |
| 工作区导入 pdf/docx | `kind=document`，自动入队 |
| 工作区导入其它类型 | `kind=other`，进 `other/`，不解析 |
| 用户重试 | `failed` / `partial` 可 `reparse`；覆盖同 `file_id` 产物，`attempt++` |

---

## 6. 页面与 API

### 6.1 路由

| 路由 | 用途 |
|---|---|
| `/workspaces` | 工作区列表（一任务一工作区） |
| `/workspaces/:taskId` | 文件列表 + 树浏览 |

导航增加「工作区」。任务详情页增加「打开工作区」链接，指向 `/workspaces/:taskId`。

### 6.2 列表页

展示：任务 ID、招标/标书文件名（或主标签）、文件数、解析汇总（成功/进行中/失败）、创建时间。点击进入详情。

### 6.3 详情页布局

1. **上：文件区** — 标签、原文件名、类型、解析状态、更新时间；操作：下载原件、重试解析；「导入文件」（文件 + 自由标签）；存在 `pending`/`running` 时约 2s 轮询
2. **下：阅读区** — 选中已 `succeeded`/`partial` 的文件后：左侧可折叠文档树，右侧章节 Markdown（按 tree offset 切片；表格占位可预览 HTML）。未选中或未完成时展示状态/错误摘要

复用现有 `MarkdownPreview`。

### 6.4 API

前缀：`/api/workspaces`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 工作区列表 |
| GET | `/{task_id}` | 详情（文件列表 + 状态） |
| POST | `/{task_id}/files` | 导入（multipart：`file` + `label`） |
| GET | `/{task_id}/files/{file_id}` | 单文件元数据 |
| GET | `/{task_id}/files/{file_id}/tree` | 文档树 JSON |
| GET | `/{task_id}/files/{file_id}/content?node_id=` | 章节 Markdown 切片 |
| POST | `/{task_id}/files/{file_id}/reparse` | 重试解析 |
| GET | `/{task_id}/files/{file_id}/download` | 下载原件 |
| GET | `/{task_id}/index` | 返回 `index.md` 原文 |

图片与 table HTML 通过受控文件接口或静态挂载供预览。

---

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| 扩展名非法 | 导入 400（解析主路径：`.pdf` / `.docx`） |
| 文件过大 | 400，沿用现有上限 |
| convert 失败 | Job `failed`，文件 `parse_status=failed`，可重试 |
| 单表抽取失败 | warning；文件可为 `partial`；其余阶段继续；可重试 |
| 无有效标题 | 单节点全文树 + warning；继续 chunk；无其它失败时为 `succeeded` |
| TOC 与正文严重不一致 | 正文标题+序号优先；warnings；不中断 |
| 进程重启 | 解析中 Job 重新 `queued` |
| 任务不存在 | 404 |
| 无效 `node_id` | content API 404 |

用户可见状态文案与简短错误；`partial` 提示「部分成功，可重试」。

---

## 8. 测试与验收

### 8.1 后端（pytest）

1. 导入后路径符合 Artifact 布局；`index.md` 含对应条目
2. 创建任务后自动为招标/标书创建 WorkspaceFile + ParseJob
3. **目录树夹具：** 含目录页 + 正文标题 + 序号的样例 —— TOC 区不进切分；`subtree_end` 覆盖子孙；序号层级正确
4. DOCX 合并单元格 → HTML 含 rowspan/colspan；模拟单表失败 → `partial` + reparse
5. content API 返回文本落在节点 offset 范围内
6. 重试后状态与产物更新

### 8.2 前端手工验收

1. `/workspaces` 可见任务并进入详情
2. 解析完成后：点文件 → 左树右文，切换章节内容变化
3. 自由标签导入 pdf/docx 会入队解析
4. `failed`/`partial` 可重试

### 8.3 本期不测

检索质量、真实 LLM、全量复杂扫描件 OCR。

---

## 9. 实现顺序建议

1. 磁盘布局约定 + WorkspaceFile/ParseJob 模型与迁移/建表
2. 创建任务挂钩：迁入 `document/`、入队 ParseJob
3. Parse scheduler + convert/extract 最小可用（先 DOCX，再 PDF）
4. build_tree + chunk + index.md（含 TOC 夹具测试）
5. 表格高质量路径与 partial/重试
6. Workspace API
7. 前端 `/workspaces` 列表与详情（文件列表 + 树 + 内容）
8. 报告同步到 `report/`（若与现有报告路径衔接需要）

---

## 10. 后续（非本期）

- 基于 `chunks.json` 的关键字检索与向量检索
- 解析完成后供诊断/解读引擎按章节取证
- 可选外部文档 API 增强复杂 PDF 表格
