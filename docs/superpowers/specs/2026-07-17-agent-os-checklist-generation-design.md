# Agent OS 检查项生成接入设计

**日期：** 2026-07-17  
**状态：** 已批准  
**范围：** 将 `generating_checklist` 阶段的大模型调用切换为已发布的 Agent OS 应用，删除 Mock 检查项生成实现，并将智能体配置持久化到 `docs/agents_config`  
**前置：**

- `2026-07-17-tender-checklist-generation-design.md`
- `2026-07-17-agent-os-tender-interpretation-design.md`

---

## 1. 目标

1. 用 `agent-create-publish` 创建并发布检查项生成智能体与 api 应用。
2. 将调度链路中的 `MockChecklistAgent` 替换为 `AgentOSChecklistAgent`。
3. 长文档仍按分片多次调用；短文档一次调用；跨分片合并与校验留在后端。
4. 将已发布契约快照写入 `docs/agents_config/tender_checklist_generator.json`。
5. 删除检查项生成相关的 Mock 代码与接线；失败不静默降级。

### 1.1 成功标准

1. `generating_checklist` 只通过 Agent OS 应用 `tender_checklist_generator_app` 生成检查项。
2. 每次调用使用显式输入字段，不依赖可解析的拼装前缀字符串。
3. 单次调用返回近终态 `categories + items`；后端完成跨片合并、ID 重写与现有 `validate_draft`。
4. 智能体与应用已发布，配置落盘于 `docs/agents_config/`。
5. `MockChecklistAgent` 及引用已移除；默认自动化测试不依赖真实 Agent OS。

### 1.2 本期不做

- 不新增第二个「合并专用」智能体。
- 不实现 chat / aichat 入口。
- 不改变诊断引擎与召回实现（仍按既有检查项清单批诊断）。
- 不在 Agent OS 失败时回退 Mock。
- 不把 `docs/agents_config/*.json` 当作运行时密钥或连接配置来源。

---

## 2. 方案选择

采用「镜像解读侧」方案：

- 复用或按解读规格落地通用 `AgentOSClient.invoke_app`。
- 业务适配器 `AgentOSChecklistAgent` 实现现有 `ChecklistAgent` 协议。
- 用 `agent-create-publish` 发布 api/sync 应用，配置持久化到 `docs/agents_config`。

未采用：

- 薄包装解析 `stable_prefix`：与显式字段目标冲突，脆弱。
- 单次大上下文由模型完成跨片合并：违背「单智能体提取 + 后端合并」决策，超时与成本更高。

---

## 3. 智能体与 IO

通过 `agent-create-publish` 发布：

| 项 | 值 |
|---|---|
| zhName | 招标诊断检查项生成助手 |
| enName | `tender_checklist_generator` |
| 应用 name | 招标诊断检查项生成 |
| 应用 enName | `tender_checklist_generator_app` |
| mode | `api` |
| apiConfig | `{ "syncType": "sync" }` |
| 调用 | `POST /v1/apps/invoke`，`appName=tender_checklist_generator_app` |

### 3.1 输入

每次分片或全文各调用一次，字段固定：

| 字段 | required | 含义 |
|---|---|---|
| `system_instructions` | 是 | 仓库内固定的生成规则与输出 schema 约束文案；各次调用字节级相同，利于前缀缓存 |
| `interpret_report` | 是 | 完整解读报告 Markdown |
| `admin_config` | 是 | 管理端配置 JSON 字符串（`sort_keys=True`） |
| `tender_segment` | 是 | 当前招标正文分片；短文档为全文 |

与 Agent OS `prompt.systemPrompt` 的分工：

- `systemPrompt`：智能体角色、边界、如何消费四个输入字段、输出必须贴合 schema。
- `system_instructions`：作为结构化输入每次显式传入的稳定规则块（与 ContextBuilder 常量同源）；不从 `systemPrompt` 运行时拆解。

### 3.2 输出

近终态结构化对象，与现有草稿模型对齐：

- `schema_version`：固定 `"1"`，与 `CHECKLIST_SCHEMA_VERSION` 一致
- `categories[]`：`id`, `name`, `description`, `retrieval_query`, `expected_locations`, `sort_order`
- `items[]`：与 `ChecklistItemDraft` 字段一致，包括 `importance`、`source_references`、`retrieval_hints`、`expected_evidence`、`compliance_rules`、`consequence_rules`、`admin_config_refs` 等

约定：

- 单次调用只覆盖**本分片**内容；跨分片本地 ID 允许冲突，由后端合并时重写。
- `source_references` 必须指向本分片内可追溯位置（沿用现有坐标约定：`section` / `start` / `end` / `segment_index` 等）。
- 无招标依据的推断项不得生成。

### 3.3 模型与 runtime 倾向

- 模式：结构化抽取 → 默认 `temperature=0.3`，`formatInput` / `formatOutput` 为 `true`
- 应用 `timeoutMs=180000`（与解读侧同级；草案确认时可上调，不得低于该值除非联调证明足够）
- 智能体 runtime：`streaming=false`，`multiTurn=false`；传输层重试由 `AgentOSClient` 控制

`agent-create-publish` 草案若修改上述默认值，以确认后的草案为准，并同步写入配置快照。

---

## 4. 组件设计

### 4.1 ChecklistContextBuilder

改造主路径，不再把解读报告与管理配置拼进 `stable_prefix` 作为唯一调用载荷。

产出：

```text
ChecklistCallInput:
  system_instructions: str
  interpret_report: str
  admin_config: str
  tender_segment: str
  segment_index: int
```

分片策略保持不变：

- `CHECKLIST_SINGLE_PASS_TOKENS`
- `CHECKLIST_CHUNK_TOKENS`
- `CHECKLIST_CHUNK_OVERLAP_TOKENS`

上下文对象（可保留 `PromptContext` 名或等价重命名）需继续提供：

- `calls: list[ChecklistCallInput]`
- 供 `validate_draft` 使用的分片列表与坐标空间

`system_instructions` 为仓库内固定文案，内容覆盖角色、禁止无依据推断、输出 schema、重要性与判定规则枚举约束。

### 4.2 AgentOSClient

本需求范围内必须具备可用的通用客户端。若仓库尚无实现，按 `2026-07-17-agent-os-tender-interpretation-design.md` 在本需求内落地：

```python
async def invoke_app(
    app_name: str,
    input_data: dict[str, object],
) -> dict[str, object]:
    ...
```

职责限于传输、鉴权、超时、有限重试与 JSON 解码。不绑定业务应用名。

连接配置优先级：环境变量 → 项目根 `config.local.json` 的 `agentOs` → 非敏感默认值。`AGENT_OS_BASE_URL` 缺失时在首次实际调用失败，不阻止应用启动。

### 4.3 AgentOSChecklistAgent

实现 `ChecklistAgent.generate(task_id, context) -> ChecklistDraft`。

职责：

1. 默认 `app_name = "tender_checklist_generator_app"`（构造可注入，便于测试）。
2. 对每个 `ChecklistCallInput` 调用一次 `invoke_app`，映射显式输入字段。
3. 将每次响应解析为局部 `ChecklistDraft`；缺字段或类型错误立即失败。
4. 调用跨片合并，得到全局 `ChecklistDraft`。
5. `agent_type = "agent_os"`；`agent_version` 使用字符串 `"1"`（与 `config.CHECKLIST_AGENT_VERSION` 一致）。发布版本号记录在 `docs/agents_config` 快照的 `publishedVersion`，不写入该字段。
6. 不回退 Mock。

### 4.4 跨片合并

独立纯函数（便于单测），规则：

1. 按调用顺序收集各片 `categories` / `items`。
2. **分类合并键**：规范化后的 `name`；同名合并，`expected_locations` 与 `retrieval_query` 去重拼接，保留首次 `description`。
3. **检查项去重键**：规范化 `(title, requirement)`；保留首次出现（更早分片优先）。
4. **重写 ID**：全局 `category-001…` / `item-001…`，重绑 `category_id` 与 `sort_order`。
5. 若合并后某分类超过 `CHECKLIST_MAX_ITEMS_PER_CATEGORY`：按 `source_references` 主 `section`（或等价位置线索）拆成更细分类；仍超限则交给现有校验失败路径。
6. `raw_response` 保存各片原始响应与合并后最终结构，供排查；不作为正式清单。

### 4.5 ChecklistService 与 Scheduler

编排不变：

```text
等待正文 → build context → agent.generate → validate_draft → 原子写入 → diagnosing
```

调度器改为注入 `AgentOSChecklistAgent`。`config.CHECKLIST_AGENT` 默认 `"agent_os"`，不再提供 mock 切换开关。

### 4.6 删除范围

- 删除 `backend/app/engine/checklist_mock.py`
- 删除 `scheduler`、测试中对 `MockChecklistAgent` 的引用与 patch
- 原依赖 Mock 确定性抽取行为的用例改为测合并/解析/适配器映射，或删除无关断言

---

## 5. 配置持久化

发布成功后写入：

`docs/agents_config/tender_checklist_generator.json`

结构对齐 `docs/agents_config/tender_doc_interpreter.json`，至少包含：

- `agent`：id、zhName、enName、publishedVersion 等
- `application`：id、enName、mode、apiConfig、timeoutMs、publishStatus 等
- `invoke`：method、path、appName、requiredInputs / optionalInputs
- `model`：modelId、temperature、thinking 等
- `io`：inputSchema / outputSchema
- `prompt`：systemPrompt（及必要的说明）

运行时以代码内默认 `appName` + 本地 Agent OS 连接配置为准。`docs/agents_config` 是已发布契约与溯源，不是密钥仓库。

---

## 6. 错误处理与停止语义

可重试（客户端，最多 `AGENT_OS_MAX_ATTEMPTS`）：

- 连接失败、超时
- HTTP 429、502、503、504

不可重试：

- 其他 HTTP 4xx
- 响应非 JSON 对象
- 单次输出缺少必需结构或字段类型错误
- 合并后 `validate_draft` 失败

失败后：

- 不写入半成品正式分类/检查项
- 保存 raw 与错误信息
- 任务 `failed`，允许在正文解析成功时 `checklist/retry`

停止语义与解读侧一致：请求发出后无法保证终止远端推理；迟到响应返回时若任务已 `stopped`，丢弃结果，不覆盖状态，不记为调用失败。

日志与异常可包含应用名、错误类别、HTTP 状态；不得包含鉴权值或招标全文。

---

## 7. 测试策略

### 合并逻辑

- 同名分类合并与去重
- 检查项 `(title, requirement)` 去重与分片优先
- ID 重写与 `category_id` 重绑
- 超限分类拆分与仍超限失败

### AgentOSChecklistAgent

- 显式传入 `tender_checklist_generator_app`
- `input` 含四字段且与 `ChecklistCallInput` 一致
- 非法/缺字段响应失败
- 多分片时调用次数等于分片数

### AgentOSClient（若本需求内落地）

- 沿用解读侧客户端测试要求：URL、`appName`、鉴权、重试、非 envelope 响应

### Scheduler / API

- 注入假适配器或 mock `invoke_app`
- 生成失败 → `failed` + 可 retry
- stop 后忽略迟到成功响应
- 不再 import 或 patch `MockChecklistAgent`

默认自动化测试使用 Mock HTTP transport，不依赖真实 Agent OS。允许环境开关启用的手动联调，但不纳入默认套件。

---

## 8. 实现顺序建议

1. （若缺失）落地 `AgentOSClient` 与连接配置。
2. 改造 ContextBuilder 为显式 `ChecklistCallInput`。
3. 实现合并纯函数与单测。
4. 用 `agent-create-publish` 发布智能体/应用，写入 `docs/agents_config/tender_checklist_generator.json`。
5. 实现 `AgentOSChecklistAgent` 并切换 scheduler。
6. 删除 Mock 及相关测试接线，更新回归测试。

步骤 4 遵守 `agent-create-publish` 硬门禁：用户确认发布草案前不得调用写接口。

---

## 9. 验收标准

1. 生产请求命中 `/v1/apps/invoke`，`appName` 为 `tender_checklist_generator_app`。
2. 输入字段为 `system_instructions`、`interpret_report`、`admin_config`、`tender_segment`。
3. 短文档一次调用、长文档按分片多次调用，后端合并后通过现有校验并落库。
4. `docs/agents_config/tender_checklist_generator.json` 存在且与已发布应用一致。
5. 代码库中不再存在检查项 `MockChecklistAgent` 实现与生产接线。
6. Agent OS 或校验失败时任务失败且可按现有 API 重试；不降级 Mock。
7. 默认测试不依赖外部 Agent OS。
