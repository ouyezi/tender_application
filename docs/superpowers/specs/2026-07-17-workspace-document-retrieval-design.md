# 工作区文档内容查找设计规格

**日期：** 2026-07-17  
**状态：** 设计已确认，待用户复核正文  
**范围：** 工作区内文档索引构建，以及按诊断项内容来源类型分流的检索能力；首要消费方为诊断引擎 `RetrievalProvider`  
**前置：**

- `2026-07-16-workspace-management-design.md`（解析、章节树、`chunks.json`）
- `2026-07-17-tender-checklist-generation-design.md`（`RetrievalProvider` 协议与检查项检索字段）
- `2026-07-17-agent-os-tender-interpretation-design.md`（Agent OS 调用形态，本期复用同一接入方式）

---

## 1. 目标

在招标文件处理与标书诊断过程中，能够快速、准确地定位工作区内相关文档内容。检索不是单一混合搜索，而是按诊断项声明的**内容来源类型**分流：

1. 要求招标文件全文 → 直接返回招标全文。
2. 要求全部授权证书 / 资质证明 → 按受控标签返回集合。
3. 要求标书全文或大范围章节 → 返回粗粒度大片段 ID，供后续智能体拆分处理。
4. 要求语义定位（如是否支持 7 天无理由）→ 走查询重写、三路召回与重排。

### 1.1 成功标准

1. 解析成功的工作区文档能建成双粒度索引（`fine` + `large`），并支持索引未完成时查询已就绪部分。
2. 四类 `content_source` 行为正确；全文 / 集合 / 大段路径**不**走三路召回与重排。
3. 命中父章节（如「技术方案」）时，默认返回覆盖其子树的 `large` 段，并附带 `child_chunk_ids`。
4. 集合型仅靠受控标签过滤即可召回整类材料（如全部授权证书）。
5. `precise_search` 完成：查询重写 → 向量 / 关键字 / Wiki 三路召回 → 权重与 BM25 重排 → AI 重排；模型失败可降级并标记 `degraded`。
6. OCR 页与表格文本可进入索引；单页 OCR 失败不阻断其余内容索引。
7. 检索严格限定在单个 `task_id` 内；真实实现可替换 checklist 设计中的 Mock `RetrievalProvider`，不改变批量诊断协议。

### 1.2 本期不做

- 工作区搜索 UI。
- 外部检索服务（Elasticsearch、Qdrant 等）。
- 直接生成自然语言答案（只返回全文、集合、大段或知识块引用）。
- 自由标签体系（摘要打标必须映射到受控词表）。
- 查询时动态生成 Wiki（仅在索引阶段预生成）。
- 未解析的 `other` 文件、纯图片附件（无 OCR 管线接入前）的检索。
- 跨任务全局知识库。

---

## 2. 约束与现状

### 2.1 部署与规模

- 本地单机：SQLite 元数据 / FTS5、本地向量索引、本地 Embedding 模型。
- 典型工作区：2–5 个已解析 PDF/DOCX；主标书可能数百 MB～1GB+，含大量图片，页数可达 1000+。
- 无 Celery/Redis；索引构建挂钩现有 `parse_scheduler` 完成事件，异步推进 OCR 与索引。

### 2.2 可复用现状

- 解析管线已产出 `markdown`、`tree.json`、`chunks.json`。
- 现有切块按**叶子章节**生成；超长叶子再按段落窗口切分（默认约 4000 字符）。
- 文档树节点具备 `start_offset` / `end_offset` / `subtree_end`，阅读器已能按子树取整章内容。
- 父章节在仅有子节点时通常**没有**独立 fine chunk；若检索层不维护大段，会出现「命中标题/概要但子章节正文缺失」。

### 2.3 AI 运行时

- 知识块摘要打标、Wiki 生成、查询重写、AI 重排：统一通过 Agent OS 应用调用。
- Embedding：本地模型，数据不出工作区机器。

---

## 3. 总体架构

```text
ParseJob 成功 / partial
  →（含 OCR 的正文进入 markdown）
  → KnowledgeIndexer
      → knowledge_chunks（fine + large）
      → FTS5 关键字索引
      → 本地向量索引
      → ChunkEnricher（标题 / 概要 / 描述 / 受控标签）
      → WikiBuilder（主题页，索引时预生成）

Diagnosis
  → RetrievalProvider.retrieve(...)
      → 按 content_source 分流
          full_document
          collection
          large_segments
          precise_search
```

### 3.1 模块职责

| 模块 | 职责 | 不负责 |
|------|------|--------|
| `KnowledgeIndexer` | 解析完成后建/重建索引；维护索引进度与 partial 可查 | 诊断业务规则 |
| `ChunkEnricher` | Agent OS：块级标题、概要、描述、受控标签映射 | 召回排序 |
| `WikiBuilder` | Agent OS：按标签/主题预生成 Wiki 页并指向知识块 | 集合型权威召回（权威仍是标签过滤） |
| `RetrievalProvider` | 诊断唯一检索入口；按 `content_source` 分流 | 解析、写索引 |
| `QueryRewriter` | 仅 `precise_search`：产出向量句、关键字、Wiki 查询 | 其他三类分流 |
| `AiReranker` | 仅 `precise_search`：flash 模型对候选块标题+概要重排 | 全文/集合/大段路径 |

### 3.2 与检查项生成的关系

- checklist 设计中的 `RetrievalProvider` 由 Mock 替换为本规格真实实现，调度器与 `BatchDiagnosisEngine` 协议不变。
- 检查项新增显式字段 `content_source` 与 `content_target`（见 §5）。
- 现有 `retrieval_query` / `retrieval_hints` / `expected_locations`：在 `precise_search` 时作为查询输入；其他三类以 `content_source` + `content_target` 为准，不触发三路召回。

---

## 4. 双粒度知识块与父子展开

### 4.1 Fine 块

- 沿用现有叶子切块逻辑；超长叶子再切。
- 用途：打标、向量 / 关键字索引、`precise_search` 候选、集合过滤的基本单元（也可挂在 large 下）。
- 字段要点：`chunk_id`、`node_id`、`parent_node_id`、`title_path`、`ancestor_node_ids`、`start`、`end`、`text`（或外置引用）、`source`（`native_text` | `ocr` | `table`）。

### 4.2 Large 块

- 对每个**有子节点**的章节节点生成一条 large 段：正文范围为 `start_offset → subtree_end`。
- 用途：
  - 命中父章节时的默认返回形态；
  - `large_segments` 的主返回列表；
  - 供后续智能体按大段 ID 拆分任务。
- 每条 large 记录 `child_chunk_ids`（下属 fine 块，按文档顺序）及自身标题路径、概要。

### 4.3 父章节命中规则（已确认）

> **注（2026-07-20）：** `precise_search` / `collection` 的 fine→large 展开行为已由 `2026-07-20-retrieval-parent-context-resolver-design.md` 替代。

命中父章节（标题、概要、标签或 Wiki 指向该节点）时：

- **默认展开为覆盖子树的 `large` 段返回**；
- 同时附带 `child_chunk_ids`，调用方可再拆细块；
- 禁止只返回父标题与简要描述而省略子章节正文范围。

### 4.4 全文型与切块的关系

- `full_document` **不依赖** fine/large 切块，直接读取目标文件 `md_path` 全文（或等价全文引用句柄）。

---

## 5. 检查项内容来源声明

检查项（或分类级覆盖字段，若未来需要）必须显式声明：

```text
content_source: full_document | collection | large_segments | precise_search

content_target:
  file_role?: tender | bid | any     # full_document / large_segments
  target_tags?: string[]             # collection，受控词表
  root_node_id?: string              # large_segments 可选，限定子树
  query?: string                     # precise_search 可选补充
```

- 缺省 `content_source` 视为配置错误，系统不自动猜测类型。
- 标签必须来自受控词表；非法标签返回配置错误并列出合法值。

### 5.1 受控标签

- 预定义招标诊断常用标签（如授权证书、资质证明、营业执照、售后政策、退款政策等），实现阶段给出初始词表并可配置扩展。
- `ChunkEnricher` 将模型输出映射到词表；未命中不强制造标签。
- 每条标签带置信度；集合召回可配置最低置信度阈值。
- 一个知识块可多标签。

---

## 6. 数据模型

### 6.1 `knowledge_chunks`

| 字段 | 说明 |
|------|------|
| `id` | 主键 |
| `task_id` / `file_id` | 工作区与文件边界 |
| `chunk_id` | 与产物一致的稳定 ID |
| `node_id` / `parent_node_id` / `ancestor_node_ids` | 树定位 |
| `segment_level` | `fine` \| `large` |
| `title` / `summary` / `description` | 富化字段 |
| `tags` | 受控标签及置信度（JSON） |
| `title_path` | 面包屑标题路径 |
| `start` / `end` | markdown 字符偏移 |
| `text_ref` 或 `text` | 正文或外置路径，避免 SQLite 膨胀 |
| `child_chunk_ids` | large 专用 |
| `source` | `native_text` \| `ocr` \| `table` |
| `index_status` | `pending` \| `ready` \| `failed` |
| `embedding_status` | 向量是否就绪 |

### 6.2 `knowledge_tags`

受控词表：`id`、`name`、`aliases`（可选，仅用于映射，不对外自由扩展）、`description`、`enabled`。

### 6.3 `wiki_pages`

| 字段 | 说明 |
|------|------|
| `id` / `task_id` | |
| `title` / `summary` / `description` | 主题页内容 |
| `tags` | 关联受控标签 |
| `member_chunk_ids` | 可定位到知识块 |
| `created_at` / `updated_at` | |

Wiki 用于 `precise_search` 一路召回与可读导航；**集合型权威召回以标签过滤为准**，不以 Wiki 成员列表为唯一真相（两者应尽量一致，冲突时以标签查询结果为准）。

### 6.4 索引任务状态

按 `task_id` + `file_id`（及可选任务级汇总）维护：

`pending | running | partial | ready | failed`

检索响应必须能反映该状态。

### 6.5 本地索引文件

- 关键字：SQLite FTS5（中文分词策略在实现计划中确定）。
- 向量：按 `task_id` 隔离的本地向量索引文件 + 本地 Embedding 模型配置（路径、维度、模型名）。
- 大段 / 长正文优先外置存储，DB 存偏移或路径。

---

## 7. 索引构建流程

### 7.1 触发

1. `ParseJob` 进入 `succeeded` 或 `partial`（正文与 chunks 可用）后，`KnowledgeIndexer.enqueue(task_id, file_id)`。
2. 文件重解析：先将旧索引标记失效并删除对应条目，再重建。
3. 大文件：OCR 与索引按页/批异步推进；已完成批次标记 `ready`，任务级可为 `partial`。

### 7.2 步骤

1. 读取 markdown、tree、chunks.json（及表格抽取产物）。
2. 写入 / 更新 fine 知识块。
3. 为有子节点的章节写入 large 知识块（`start → subtree_end`）。
4. 将表格转为可检索文本，并入所属章节块，保留 `table_id` / 原表路径。
5. 调用 `ChunkEnricher` 生成标题、概要、描述并映射受控标签。
6. 构建 / 更新 FTS5 与本地向量索引。
7. 调用 `WikiBuilder` 按标签/主题预生成或更新 Wiki 页。

### 7.3 OCR

- 首版必须支持扫描页 OCR；OCR 文本进入 markdown 后再建树与切块。
- 知识块 `source=ocr` 可区分来源。
- 单页失败记 warning，不阻断其他页；整体可为 `partial`。
- 索引未完成时允许查询已完成部分，响应 `incomplete=true` 并附进度信息。

### 7.4 表格

- 首版必须将表格纳入可检索文本，并保留定位，避免只索引占位注释。

---

## 8. 检索流程

### 8.1 统一入口

```text
RetrievalProvider.retrieve(
  task_id,
  *,
  content_source,
  content_target,
  item_hints=None,   # retrieval_query / retrieval_hints 等，仅 precise_search 使用
) -> RetrievalResult
```

`RetrievalResult` 至少包含：

- `mode`：实际分流类型
- `items`：结果列表
- `index_status`：`ready | partial | unavailable`
- `incomplete`：是否因索引未完成而不完整
- `degraded`：是否发生精确查找链路降级（默认 false）
- 单条命中常用字段：`id`、`file_id`、`node_id`、`segment_level`、`title`、`summary`、`tags`、`title_path`、可选 `text` / `ref`、`child_chunk_ids`

### 8.2 `full_document`

1. 按 `content_target.file_role` 解析目标 `WorkspaceFile`（`tender` / `bid`）。
2. 要求解析成功或 partial 且存在 `md_path`。
3. 直接返回全文或全文引用句柄 + 文件元数据。
4. 目标不存在或未就绪 → `unavailable` / 明确错误；**不降级**为片段检索。

### 8.3 `collection`

1. 校验 `target_tags` 均在受控词表内。
2. 在 `task_id` 下按标签（及置信度阈值）过滤知识块。
3. 若命中对应父章节节点，按 §4.3 展开为 `large` 返回。
4. 不走查询重写、三路召回、AI 重排。
5. 零命中返回空集合，并给出可诊断原因（无标签 / 索引未就绪）。

### 8.4 `large_segments`

1. 按 `file_role` 限定文件（常见为标书）。
2. 返回该文件下 large 段列表（文档树序）；若指定 `root_node_id`，仅返回该子树范围。
3. 每条包含 large id、标题路径、概要、`child_chunk_ids`。
4. 不走三路召回与重排。

### 8.5 `precise_search`

唯一使用完整检索链路的类型：

```text
QueryRewriter（Agent OS）
  → { vector_query, keywords, wiki_query }
     ├─ 向量召回
     ├─ 关键字召回（FTS5 / BM25）
     └─ Wiki 召回（主题页 → 成员块）
  → 合并去重
  → 权重 + BM25 重排
  → AiReranker（flash：标题+概要+查找要求 → id/标题排序）
  → Top-K；父章命中则展开为 large
```

- 分类级批量诊断：允许分类级一次重写与召回，多项共享候选，再按检查项做 AI 重排（与 checklist「按分类召回」方向一致）。
- Agent OS 重写或 AI 重排失败：降级为向量 + 关键字合并结果，标记 `degraded=true`；不改为 `full_document` / `collection` 语义。

---

## 9. 错误处理

| 场景 | 行为 |
|------|------|
| 索引 `partial` | 返回已就绪结果，`incomplete=true`，附进度 |
| 索引全无 | `unavailable`，提示等待或重试索引 |
| `full_document` 目标未就绪 | 失败，不降级检索 |
| `collection` 非法标签 | 配置错误，列出合法标签 |
| 缺少 `content_source` | 配置错误 |
| `precise_search` 模型失败 | 检索链路内降级，`degraded=true` |
| OCR 单页失败 | 其他页继续；不假装全集完整 |
| 跨 `task_id` | 拒绝 |

原则：全文 / 集合 / 大段路径不因模型不可用而改走语义检索；仅 `precise_search` 允许链路内降级。

---

## 10. 测试要点

| 类型 | 覆盖 |
|------|------|
| 单元 | 双粒度生成、父章展开为 large、标签过滤、四类分流路由 |
| 索引 | 解析完成后触发；重解析失效重建；partial 可查 |
| OCR / 表格 | 扫描页入索引；表格文本可被关键字命中 |
| 精确查找 | 三路合并去重、重排、AI 重排失败降级 |
| 集成 | 模拟诊断项：全文、资质集合、大段、语义问 |
| 隔离 | 跨 `task_id` 不可见 |

---

## 11. 配置要点（实现计划细化）

- Agent OS：打标、Wiki、查询重写、AI 重排应用标识与超时。
- 本地 Embedding：模型路径、维度、批大小。
- 受控标签初始词表与置信度阈值。
- 索引并发、OCR 批大小、`precise_search` Top-K、各路召回权重。

---

## 12. 方案选择摘要

采用「分层索引 + 按来源类型分流」：

- 本地 SQLite / FTS5 / 本地向量，满足单机约束。
- 索引时预生成 Wiki；集合权威靠受控标签。
- 精确查找才支付完整召回与 AI 重排成本。
- 双粒度解决父章节「只有标题没有子章正文」问题。

未采用「一律混合检索靠重写隐式分流」（成本高、全文/集合不稳定），也未采用「首版去掉精确查找」（无法覆盖售后/退款类语义问题）。
