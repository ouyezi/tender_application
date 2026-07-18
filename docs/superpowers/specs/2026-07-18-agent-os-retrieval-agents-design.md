# Agent OS 文档检索智能体接入设计

**日期：** 2026-07-18  
**状态：** 设计已确认，待用户复核正文  
**范围：** 为工作区文档检索链路中四个 AI 步骤创建并发布 Agent OS 应用，替换生产路径 Mock，强制复用 `AgentOSClient`  
**前置：**

- `2026-07-17-workspace-document-retrieval-design.md`（分流检索与四类 AI 步骤）
- `2026-07-17-agent-os-tender-interpretation-design.md`（`AgentOSClient` 与 invoke 形态）
- `2026-07-17-agent-os-checklist-generation-design.md`（api/sync 应用、契约落盘、去 Mock 模式）

---

## 1. 目标

1. 用 `agent-create-publish` 创建并发布文档检索所需的 **4 个** Agent OS 智能体与 api 应用：知识块富化、Wiki 页文案、查询重写、AI 重排。
2. 生产路径去掉对应 Mock；适配器一律通过已有 `AgentOSClient.invoke_app` 调用；失败硬失败（不降级、不回退 Mock）。
3. 契约快照分别写入 `docs/agents_config/`；在 `README.md` 明确：**凡调用智能体必须复用 `AgentOSClient`，禁止业务侧自建 HTTP**。
4. 不改变检索分流协议（`full_document` / `collection` / `large_segments` / `precise_search`）及诊断批量协议。

### 1.1 成功标准

1. 四个应用均已发布为 `api` + `sync`，可经 `POST /v1/apps/invoke` 调用。
2. 索引路径：`ChunkEnricher` / `WikiBuilder` 走真实应用；Wiki 仍由后端按受控标签聚合成员，智能体只生成 `title` / `summary` / `description`。
3. 精确查找：`QueryRewriter` / `AiReranker` 走真实应用；失败则整次检索失败（本期覆盖检索规格中的 `degraded` 降级路径）。
4. 生产无四个 Mock 接线；单测用注入假 `invoke_app` / fixture，默认自动化测试不打真实 Agent OS。
5. `README.md` 含强制复用客户端的说明；四份契约落在 `docs/agents_config/`。

### 1.2 本期不做

- 工作区搜索 UI、外部检索引擎、自由标签体系。
- chat / aichat 入口；新增「合并专用」第五智能体。
- 改变 `RetrievalProvider` 四类分流语义（仅替换 AI 步骤实现）。
- 把 `docs/agents_config/*.json` 当作运行时密钥或连接配置来源。
- 新建第二个 HTTP 客户端；`AgentOSClient` 已由解读/检查项工作落地，本期只复用与约束文档化。

---

## 2. 方案选择

采用「四应用镜像检查项模式」：

- 每个 AI 步骤一个 `api`/`sync` 应用，独立应用名、超时与模型倾向。
- 后端适配器只做 IO 映射、响应校验与落库；统一 `AgentOSClient.invoke_app`。
- Wiki：后端聚合权威成员列表，模型只写文案。

未采用：

- 单应用 `op` 分发：提示词与 schema 臃肿，难以独立选型。
- 索引侧 / 查询侧两应用合并：超时与温度难兼顾，收益有限。

---

## 3. 智能体与 IO

共性：

- `mode=api`，`apiConfig.syncType=sync`
- `formatInput` / `formatOutput` = `true`
- runtime：`streaming=false`，`multiTurn=false`
- 调用：`POST /v1/apps/invoke`；`appName` 由适配器常量持有
- 一律经 `AgentOSClient.invoke_app(app_name, input_data)`
- 复杂结构字段用 `*_json` 字符串，降低嵌套 schema 不稳定风险

| # | zhName | enName | 应用 enName | 落盘 |
|---|--------|--------|-------------|------|
| 1 | 知识块富化打标助手 | `retrieval_chunk_enricher` | `retrieval_chunk_enricher_app` | `docs/agents_config/retrieval_chunk_enricher.json` |
| 2 | 检索 Wiki 页文案助手 | `retrieval_wiki_writer` | `retrieval_wiki_writer_app` | `docs/agents_config/retrieval_wiki_writer.json` |
| 3 | 精确检索查询重写助手 | `retrieval_query_rewriter` | `retrieval_query_rewriter_app` | `docs/agents_config/retrieval_query_rewriter.json` |
| 4 | 精确检索 AI 重排助手 | `retrieval_ai_reranker` | `retrieval_ai_reranker_app` | `docs/agents_config/retrieval_ai_reranker.json` |

### 3.1 ChunkEnricher

**输入**

| 字段 | required | 说明 |
|------|----------|------|
| `task_id` | 是 | 工作区任务 ID |
| `catalog_json` | 是 | 受控词表 JSON：`[{name, aliases, description}]` |
| `segments_json` | 是 | 本批：`[{chunk_id, title_path, text, segment_level}]` |

**输出**

| 字段 | required | 说明 |
|------|----------|------|
| `segments_json` | 是 | `[{chunk_id, title, summary, description, tags:[{name, confidence}]}]` |

后端：校验返回 `chunk_id` 与入参集合一致；标签再经 `map_to_controlled_tags` 过滤；未映射标签丢弃，不强造。

### 3.2 WikiWriter（`WikiBuilder` 的 AI 部分）

**输入**

| 字段 | required | 说明 |
|------|----------|------|
| `task_id` | 是 | |
| `pages_json` | 是 | 后端已聚合：`[{tag_name, member_chunk_ids, member_summaries:[{chunk_id,title,summary}]}]` |

**输出**

| 字段 | required | 说明 |
|------|----------|------|
| `pages_json` | 是 | `[{tag_name, title, summary, description}]` |

后端：按受控标签与置信度阈值聚合成员 → 调智能体 → 按 `tag_name` 合并文案后写 `WikiPage`。`member_chunk_ids` 以后端聚合为准，不以模型返回为权威。

### 3.3 QueryRewriter

**输入：** `query`（必填）、`hints_json`（必填，可为 `[]`）  
**输出：** `vector_query`、`keywords_json`（string[] JSON）、`wiki_query`

### 3.4 AiReranker

**输入：** `requirement`、`hits_json`：`[{chunk_id, title, summary, score}]`  
**输出：** `chunk_ids_json`（按相关性降序的 `chunk_id` 列表 JSON）

后端：缺 id、类型错误或含未知 `chunk_id` → 响应错误并硬失败（不静默回退原序）。

### 3.5 模型与 runtime 倾向

- 富化 / Wiki / 重写：结构化抽取，温度约 `0.2–0.4`
- AI 重排：优先更快/flash 档（若 Agent OS 有对应模型），温度约 `0.1–0.3`，`thinking=false` 优先
- 应用超时：富化/Wiki 建议 `timeoutMs=180000`；重写/重排可更短（草案确认时定，默认不低于 `60000`）
- 传输层重试由 `AgentOSClient` 控制

`agent-create-publish` 草案若修改默认值，以确认后的草案为准，并同步写入配置快照。

---

## 4. 组件设计

### 4.1 AgentOSClient（复用，不重造）

仓库已具备 `app.services.agent_os.AgentOSClient.invoke_app`。本期约束：

1. 检索四个适配器、以及后续任何智能体调用，**必须**复用该客户端。
2. 禁止在业务模块直接使用 `httpx` / `requests` 等访问 `/v1/apps/*`。
3. 在 `README.md` 增加「Agent OS 调用规范」小节，写明上述硬性要求。
4. 应用名由各适配器常量持有（例如 `RETRIEVAL_CHUNK_ENRICHER_APP_NAME`），不设全局单一应用名环境变量。

### 4.2 适配器

| 协议 | Agent OS 实现 | 默认工厂 |
|------|---------------|----------|
| `ChunkEnricher` | `AgentOSChunkEnricher` | 返回 Agent OS 实现 |
| `WikiBuilder` | `AgentOSWikiBuilder` | 同上 |
| `QueryRewriter` | `AgentOSQueryRewriter` | 同上 |
| `AiReranker` | `AgentOSAiReranker` | 同上 |

共同约定：

- 构造可注入 `client` 或 `invoke_app`，便于单测。
- 请求/响应按 §3 编解码；非法响应抛业务 `*ResponseError` 或透传 `AgentOSError`。
- 不回退 Mock。

### 4.3 WikiBuilder 修正

现有 `AgentOSWikiBuilder` 仅传 `task_id`、不落库，不符合规格。正确流程：

1. 删除该 `task_id` 下旧 `WikiPage`（与现 Mock 一致）。
2. 读取就绪 fine 块，按受控标签与置信度阈值分组。
3. 组装 `pages_json`，调用 `retrieval_wiki_writer_app`。
4. 合并文案后写入 `WikiPage`（`tags` / `member_chunk_ids` 来自后端聚合）。

### 4.4 IndexScheduler / RetrievalProvider

- `IndexScheduler`：继续通过 `get_chunk_enricher()` / `get_wiki_builder()` 取实现；默认已是 Agent OS。
- `WorkspaceRetrievalProvider.precise_search`：移除「重写/重排失败 → `degraded=true` 继续」路径；异常上抛或返回明确错误，使本次精确查找失败。
- `full_document` / `collection` / `large_segments` 仍不调用上述四个智能体。

### 4.5 Mock 处置

对齐检查项生成：

- 生产路径删除四个 Mock 类接线与 `AGENT_CHUNK_ENRICHER` 等 `mock|agent_os` 开关。
- 单测使用假适配器或注入假 `invoke_app`；需要规则行为时在测试内实现轻量 stub，不作为生产默认。
- 更新/删除依赖「默认 Mock factory」的测试（如 `test_agent_os_factories.py`）。

---

## 5. 错误处理

| 场景 | 行为 |
|------|------|
| 连接/超时/5xx（客户端重试耗尽） | 抛 `AgentOSError`；索引 job → `failed`（或文件级失败）；精确查找 → 本次检索失败 |
| 响应缺字段 / JSON 非法 / `chunk_id` 不齐 | 业务响应错误，同上硬失败 |
| 富化返回非法标签名 | 丢弃非法项，保留合法映射；若整批无有效结构仍失败 |
| Wiki 某页缺文案或 `tag_name` 对不上 | 整次 `build_for_task` 失败（不半写） |
| Agent OS 未配置 `base_url` | `AgentOSConfigError`，首次调用暴露 |
| 跨 `task_id` | 仍由检索层拒绝（与检索规格一致） |

原则：本期四个 AI 步骤均不允许静默 Mock 回退；精确查找不允许链路内 `degraded` 继续（覆盖 `2026-07-17-workspace-document-retrieval-design.md` §8.5 / §9 相关降级条款）。

---

## 6. 配置与文档

1. 发布成功后写入四份 `docs/agents_config/<enName>.json`（含 agent/application/invoke/io/model 快照）。
2. `README.md` 增加 Agent OS 调用规范：必须复用 `AgentOSClient`；列举当前应用（解读、检查项、本四者）仅作发现指引，运行时仍以适配器常量为准。
3. 连接配置仍走环境变量 / `config.local.json` 的 `agentOs`；契约 JSON 不承载密钥。

---

## 7. 测试要点

| 类型 | 覆盖 |
|------|------|
| 适配器单元 | 假 `invoke_app`：请求字段、响应解析、非法响应硬失败 |
| Wiki | 聚合逻辑单测（不调模型）；文案合并与落库 |
| Provider | 重写/重排抛错时不再标 `degraded` 继续 |
| 回归 | 默认 pytest 不依赖真实 Agent OS；factory 不再默认 Mock |
| 手工 | 发布后对各 `appName` 各打一枪 smoke |

---

## 8. 实现顺序

1. 用 `agent-create-publish` 依次发布四个应用，落盘契约。
2. 重写四个适配器（含 Wiki 聚合落库），删除生产 Mock 与开关。
3. 修改 `precise_search` 硬失败语义；更新 factory 与相关测试。
4. 更新 `README.md` 调用规范。
5. 跑单元/回归；手工 smoke 四个应用。

---

## 9. 与检索规格的关系

本设计是 `2026-07-17-workspace-document-retrieval-design.md` 的 Agent OS 落地规格：

- 保留四类 `content_source` 分流与双粒度索引。
- 将其中「通过 Agent OS 调用」的四个步骤从 Mock 换成已发布应用。
- **刻意收紧**精确查找失败策略：由「可 `degraded`」改为硬失败，以便与解读/检查项一致、避免静默劣质召回。
