# 知识检索调试台设计规格

**日期：** 2026-07-18  
**状态：** 设计已确认，待用户复核正文  
**范围：** 工作区内只读调试页面——浏览知识块、Wiki、索引状态，并对四类 `content_source` 做检索试跑与过程深钻  
**前置：**

- `2026-07-17-workspace-document-retrieval-design.md`（索引、双粒度、四类分流、`RetrievalProvider`）
- `2026-07-16-workspace-management-design.md`（工作区、章节树、阅读器）

---

## 1. 目标

面向**开发/调试**，提供独立页面，用于：

1. 浏览与筛选工作区内知识块，核对富化质量与双粒度结构。
2. 对四类 `content_source` 做检索试跑，并深钻 `precise_search` 过程（查询重写、三路召回、合并分、重排前后、AI 理由/降级）。
3. 对照 Wiki 预生成页与受控标签召回、观察索引进度（含 `partial` / `incomplete`）。

本页补齐检索规格中「本期不做：工作区搜索 UI」的缺口，但定位为**调试台**，不是业务向工作区搜索产品。

### 1.1 成功标准

1. 可从工作区进入独立路由页，四个子 Tab 完整可用。
2. 知识块可浏览、筛选、搜索，并可查看详情（标签置信度、父子/子块关系、正文预览）。
3. 检索试跑覆盖 `full_document` / `collection` / `large_segments` / `precise_search`；精确查找可展示过程深钻字段。
4. Wiki 与索引状态足以对照「标签权威 vs Wiki 成员」以及按文件的 IndexJob 进度。
5. 生产用 `RetrievalProvider.retrieve` / `RetrievalResult` 与诊断协议**不变**；调试走独立 API 与契约。

### 1.2 本期不做

- 跨任务全局知识库、自然语言问答（只返回引用/块，不生成答案）。
- 在本页触发重建索引或改写索引数据（只读观察；重解析仍走工作区现有能力）。
- 持久化试跑历史、A/B 对比存档等完整可观测性平台（仅当前一次试跑 + 复制 JSON）。
- 业务向搜索产品化、权限体系扩展。
- 污染或扩展生产 `RetrievalResult` 契约。

---

## 2. 约束与现状

- 检索后端已具备：知识块持久化、FTS5、本地向量、Wiki、四类分流与 `precise_search` 链路。
- 现有 `RetrievalResult` 仅含最终命中与 `mode` / `index_status` / `incomplete` / `degraded` / `error`，无三路与重排中间态。
- 工作区详情页已有文件树 + Markdown 阅读器；不宜把调试信息挤进同一布局。
- 无独立 HTTP 浏览/调试 API；需新增只读 REST，严格限定单个 `task_id`。

---

## 3. 方案选择

采用**独立 Debug 层 + 只读浏览 API**：

| 方案 | 结论 |
|------|------|
| 在 `retrieve(..., debug=True)` 上扩展同一结果 | 否：易污染生产契约，深钻字段会撑胖 provider |
| **独立 `DebugRetrievalService` + 浏览 API** | **是**：生产与调试分离，便于深钻与后续扩展 |
| 分两期（先结果层后过程层） | 否：与「完整调试台 + 深钻」目标不一致，易返工 UI |

---

## 4. 信息架构与页面结构

### 4.1 路由与入口

- 路由：`/workspaces/:taskId/knowledge`
- 入口：工作区详情页增加「知识检索」链接；不改变现有文件树 + 阅读器主流程。

### 4.2 页头（固定）

- 工作区名称 / `task_id`
- 任务级索引摘要：`index_status`（`ready | partial | unavailable`）、fine/large 块数量、`incomplete` 布尔值
- 四个子 Tab

### 4.3 子 Tab

| Tab | 目的 |
|-----|------|
| **知识块** | 浏览 / 筛选 / 搜索知识块，核对富化与双粒度 |
| **检索试跑** | 四类分流试跑 + `precise_search` 过程深钻 |
| **Wiki** | 预生成主题页与成员块，对照标签召回 |
| **索引状态** | 按文件 IndexJob 阶段、进度、错误；汇总就绪度 |

### 4.4 URL 状态

支持 query 同步，便于深链与跨 Tab：

- `tab=chunks|retrieve|wiki|index`
- `chunk_id=...`（打开知识块详情）
- 可选：试跑表单预填字段（实现计划细化）

---

## 5. 后端架构与 API

### 5.1 模块职责

| 模块 | 职责 | 不负责 |
|------|------|--------|
| `DebugRetrievalService` | 四类分流试跑；`precise_search` 采集过程轨迹，返回 `DebugRetrievalResult` | 诊断调度、改生产 `RetrievalResult` |
| `KnowledgeBrowseService` | 知识块分页列表、详情（含正文）、筛选；可选按 `node_id` 子树过滤 | 写入索引 |
| `WikiBrowseService` | Wiki 列表与详情（成员块摘要） | 以 Wiki 作为 collection 权威 |
| `IndexStatusService` | 任务级汇总 + 按文件 IndexJob 明细 + 块计数 | 触发重建索引 |

实现优先在现有 retrieval 内部步骤旁注入 collector / 抽取可观测步骤，避免整份复制 `_precise_search`。

### 5.2 REST

均挂在 `/api/workspaces/{task_id}/knowledge/...`，只读（除试跑 POST 为只读副作用：调用模型/检索，不写索引）。

**浏览**

- `GET /chunks` — 分页；query：`q`, `file_id`, `segment_level`, `tag`, `source`, `index_status`, `embedding_status`, `node_id`（子树过滤）, `page`, `page_size`
- `GET /chunks/{chunk_id}` — 详情 + 正文（过大可截断，返回 `text_truncated`）
- `GET /tags` — 受控标签词表（筛选与 collection 试跑）
- `GET /wiki` / `GET /wiki/{id}`
- `GET /index-status` — 任务汇总 + 每文件 job + 块计数 / embedding 就绪比例等

**试跑**

- `POST /debug/retrieve`  
  body：`content_source`, `content_target`, 可选 `item_hints`  
  响应：`DebugRetrievalResult`

### 5.3 `DebugRetrievalResult`

```text
mode, index_status, incomplete, degraded, error
items[]                    # 最终命中（对齐 RetrievalHit 字段 + 调试扩展）
trace:                     # precise_search 完整；其他 mode 可简短
  rewrite: { vector_query, keywords, wiki_query, raw? }
  channels:
    vector:  [{ chunk_id, score, title, ... }]
    keyword: [{ chunk_id, score, ... }]   # FTS / BM25
    wiki:    [{ chunk_id, score?, wiki_page_id?, ... }]
  merged:    [{ chunk_id, score, channel_flags: {vector, keyword, wiki} }]
  pre_rerank_order: [chunk_id...]
  post_rerank_order: [chunk_id...]
  ai_rerank: { used, degraded_reason?, scores_or_ranks?, rationale? }
  expansions: [{ from_fine_id, to_large_id, reason }]
path_note                  # 非 precise_search：说明未走三路召回 / 实际路径
```

非 `precise_search`：返回最终 `items` + `path_note`；`trace` 可为空或仅含「跳过的阶段」清单，避免前端分支爆炸。

### 5.4 错误约定

| 场景 | 行为 |
|------|------|
| 配置类错误（非法标签、缺必要字段、非法 `content_source`） | HTTP 400，正文含可诊断信息（如合法标签列表）。`precise_search` 缺 `query` 且无 hints、`collection` 缺 `target_tags` 视为配置错误 |
| 检索空结果、索引 partial、AI 降级 | HTTP 200 + 结果字段（`items=[]` 可与 `incomplete` / `degraded` 并存） |
| 跨 `task_id` 或不存在 | HTTP 404 |
| AI 重写/重排失败 | `degraded=true`，附 `ai_rerank.degraded_reason`，仍返回合并重排可得结果 |

---

## 6. 各 Tab 交互

### 6.1 知识块

- **布局：** 顶栏搜索 + 筛选；主区默认扁平分页表；可选「按章节树筛选」（先选文件，复用现有 tree API，点节点 → `node_id` 子树过滤）。
- **列表列：** `segment_level`、标题、`title_path` 末两级、标签（少量展示 +N）、`source`、`index_status` / `embedding_status`、文件角色。
- **搜索：** 防抖后 `GET /chunks?q=`。优先走 FTS；不可用时降级为 title/summary（及可行时的正文）字段匹配，并角标提示降级。
- **详情：** 抽屉或侧栏——元数据、tags+置信度、`child_chunk_ids`（可点跳转）、正文预览；「打开阅读器」链到工作区详情（带 `file_id`+`node_id` 深链，若现有页暂不支持则实现计划中补齐或降级为打开工作区并提示）。
- **空态：** 索引未就绪 → 引导「索引状态」Tab；无匹配 → 提示清空筛选。

### 6.2 检索试跑

- **表单：** `content_source` 四选一，动态 `content_target`：
  - `full_document`：`file_role`
  - `collection`：`target_tags` 多选（`/tags`）
  - `large_segments`：`file_role` + 可选 `root_node_id`
  - `precise_search`：`query` + 可选 hints
- **结果区：**
  1. 状态条：`mode` / `index_status` / `incomplete` / `degraded` / `error`
  2. 最终命中列表（分数、粒度、路径）；可看摘要与 expansion
  3. 过程面板（`precise_search`）：查询重写 → 三路召回（标共现）→ merged 分与 `channel_flags` → pre/post 重排对比 → AI 理由/降级 → expansions
  4. 其他 mode：`path_note` + 最终 items，过程区注明未走三路召回
- **辅助：** 复制本次请求/响应 JSON；「在知识块中打开」→ `?tab=chunks&chunk_id=`

### 6.3 Wiki

- 列表：标题、关联标签、成员块数量、更新时间。
- 详情：summary/description、成员块卡片（可进知识块详情）。
- 对照：首版**不**在 Wiki Tab 自动跑 collection diff；展示成员块供人工核对。需要对照时，在「检索试跑」用相同标签跑 `collection`，与 Wiki 成员列表并视。权威仍以标签过滤为准（与检索规格一致）；不做自动修复。

### 6.4 索引状态

- 任务汇总：overall status、fine/large 计数、embedding ready 比例、FTS 是否可用。
- 按文件表：IndexJob 的 status / stage / progress / error / 时间。
- `partial` / `failed` 高亮；可链回工作区对应文件。

---

## 7. 数据流

```text
页面挂载
  → GET .../index-status（页头摘要）

知识块 Tab
  → GET .../chunks、GET .../tags
  →（树模式）现有 GET .../files/{file_id}/tree

检索试跑
  → POST .../debug/retrieve
  → 渲染 items + trace / path_note

Wiki Tab
  → GET .../wiki、GET .../wiki/{id}

跨 Tab
  → URL query 同步 tab / chunk_id（及可选试跑预填）
```

加载与错误：各 Tab 请求独立 loading；试跑超时展示文案并保留上次成功结果；正文过大详情截断。

---

## 8. 与检索规格的关系

- 检索语义、四类分流、双粒度、父章展开、降级规则以 `2026-07-17-workspace-document-retrieval-design.md` 为准。
- 本规格只定义**消费与可视化**以及**不改动生产契约**的 debug 观测层。
- 集合型权威召回仍为受控标签过滤；Wiki 仅作导航与一路召回对照。

---

## 9. 测试要点

| 类型 | 覆盖 |
|------|------|
| Browse API | 筛选组合、`node_id` 子树、`task_id` 隔离、正文截断 |
| Debug retrieve | 四类 mode 的 `path_note`；precise 的 trace 字段齐全；AI 失败 `degraded` |
| 配置错误 | 非法标签 400 + 合法词表；缺 query / 缺 target_tags |
| 索引 partial | 页头与试跑均体现 `incomplete` / partial 状态 |
| 前端 | Tab/query 深链、空态、过程面板折叠分区、复制 JSON |

### 建议验收场景

1. `partial` 索引工作区：页头与试跑均体现不完整。
2. collection 非法标签：400 + 合法标签提示。
3. precise_search 在 AI 不可用时：`degraded=true`，仍有合并结果与降级原因。
4. 父章命中：最终 items 为 large，且 `expansions` 可追溯 fine→large。

---

## 10. 配置与实现备注

- Agent OS：试跑路径复用现有 query rewrite / AI rerank 应用；超时与诊断侧一致或略放宽（实现计划定）。
- FTS 不可用时的块搜索降级策略需在 UI 可见。
- 工作区阅读器深链若尚无 `file_id`+`node_id` 支持，实现计划中一并补齐或明确降级文案。
- 前端风格跟随现有工作区页面，不做独立营销向视觉改版。
