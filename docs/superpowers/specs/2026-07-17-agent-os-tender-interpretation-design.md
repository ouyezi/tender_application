# Agent OS 招标文件解读接入设计

## 目标

本次改造包含两个相互独立、通过接口组合的能力：

1. 封装可复用的 Agent OS 生产态应用调用客户端。
2. 将现有招标文件解读步骤从本地 Mock 切换为已发布应用
   `tender_doc_interpreter_app`。

本次只支持同步 API 应用入口 `POST /v1/apps/invoke`。客户端结构应允许后续增加
`chat` 和 `aichat`，但不提前实现未使用的接口。

## 已确认的 Agent OS 契约

配置来源：
`docs/agents_config/tender_doc_interpreter.json`。

- 应用名：`tender_doc_interpreter_app`
- 应用模式：`api`
- 发布状态：`published`
- 同步类型：`sync`
- 应用超时：180 秒
- 请求路径：`POST /v1/apps/invoke`
- 生产响应没有 `{code, data}` envelope

请求体：

```json
{
  "appName": "tender_doc_interpreter_app",
  "input": {
    "tender_text": "招标文件 Markdown 全文",
    "project_background": "项目背景与已知约束",
    "interpretation_requirements": "额外解读要求"
  }
}
```

`tender_text` 必填，另外两个字段允许为空字符串。成功响应必须是 JSON 对象，并在
顶层包含非空字符串字段 `report_markdown`。其他结构化输出字段不在本次流程中持久化。

## 当前流程与问题

任务创建时会同时启动两个后台流程：

1. `parse_scheduler` 解析招标文件和投标文件，生成 Markdown、章节树和分块文件。
2. `scheduler` 立即使用 `MockInterpretationAgent` 生成招标文件解读报告。

因此，当前解读步骤没有使用招标文件真实内容。已有
`INTERPRETATION_AGENT` 和 `INTERPRETATION_AGENT_URL` 也未实际接线。

新流程必须等待招标文件的既有解析任务完成，读取其真实 Markdown 全文，再调用
Agent OS。不得重复解析原文件，也不得在 Agent OS 失败时回退到 Mock。

## 方案选择

采用“等待并复用现有解析结果”方案。

未采用的方案：

- 解读流程自行解析原文件：会重复工作，并可能与后台解析任务竞争。
- 重构为统一工作流编排器：长期边界更完整，但本次改动范围过大。

## 组件设计

### AgentOSClient

通用客户端只处理 Agent OS 的传输协议，不包含招标文件业务逻辑。

建议公共接口：

```python
async def invoke_app(
    app_name: str,
    input_data: dict[str, object],
) -> dict[str, object]:
    ...
```

职责：

- 拼接 `{baseUrl}/v1/apps/invoke`。
- 每次调用显式接收 `app_name`，并发送 `appName`。
- 发送结构化 `input`。
- 应用可选 Cookie 或 Header 鉴权。
- 处理超时、有限重试、HTTP 状态和 JSON 解码。
- 返回生产接口的直接 JSON 对象，不按测试态 `{code, data}` envelope 解包。

`appName` 不是全局环境配置。不同业务复用客户端时，应在各自的适配器中传入自己的
应用名，避免一个全局应用名耦合所有业务。

### TenderContentProvider

内容提供器负责从既有工作区解析流程取得真实招标文件全文。

输入：

- `task_id`
- `tender_file_id`
- 停止状态查询函数

行为：

1. 查询对应 `WorkspaceFile`。
2. 当 `parse_status` 为 `pending` 或 `running` 时，按短间隔继续等待。
3. 每次等待前后检查任务停止状态。
4. `succeeded` 或 `partial` 均可继续。
5. 校验 `md_path` 存在、指向普通文件且内容非空。
6. 以 UTF-8 读取完整 Markdown 并返回。

`partial` 的解析警告继续保留在工作区，不阻断解读。以下情况必须明确失败：

- 解析状态为 `failed`。
- 等待超过配置时限。
- 工作区文件、`md_path` 或实体文件不存在。
- Markdown 为空或不可读。

### AgentOSInterpretationAgent

业务适配器实现调整后的 `InterpretationAgent` 协议，并组合通用客户端。协议输入由原来
的原始文件路径改为已解析的招标文件正文，同时补充解读要求：

```python
async def interpret(
    *,
    task_id: str,
    tender_text: str,
    background: str,
    requirements: str,
) -> InterpretationResult:
    ...
```

职责：

- 固定使用业务应用名 `tender_doc_interpreter_app`。
- 将真实全文映射到 `tender_text`。
- 将任务的 `background` 映射到 `project_background`。
- 将任务的 `requirements` 映射到 `interpretation_requirements`。
- 调用 `AgentOSClient.invoke_app()`。
- 校验并提取 `report_markdown`。
- 返回现有 `InterpretationResult`，标题保持“招标文件解读报告”。

应用名由业务适配器显式持有，不从系统环境读取。构造函数可允许注入应用名，供测试
替换使用，但生产默认值固定为上述已发布应用。

### Scheduler

调度器仍负责流程编排，不负责 HTTP 细节或文件读取规则。

新顺序：

1. 任务进入 `interpreting`。
2. 等待招标文件解析完成并取得真实 Markdown。
3. 调用 `AgentOSInterpretationAgent`。
4. 再次检查任务是否已停止。
5. 沿用现有服务保存 Markdown 和 HTML 解读报告。
6. 将任务切换为 `diagnosing`。
7. 继续现有诊断流程。

解读步骤不再选择 Mock，也不再直接依赖原始 `tender_path`。诊断引擎仍保持现状，
不属于本次改造范围。

## 配置设计

公共连接配置按以下优先级加载：

1. 环境变量。
2. 项目根目录 `config.local.json`。
3. 非敏感默认值。

环境变量：

- `AGENT_OS_BASE_URL`：Agent OS 根地址，无安全默认值，缺失时首次调用失败。
- `AGENT_OS_TIMEOUT_SECONDS`：默认 `180`。
- `AGENT_OS_MAX_ATTEMPTS`：默认 `3`。
- `AGENT_OS_AUTH_COOKIE`：可选 Cookie。
- `AGENT_OS_AUTH_HEADER_NAME`：可选 Header 名。
- `AGENT_OS_AUTH_HEADER_VALUE`：可选 Header 值。
- `TENDER_PARSE_WAIT_TIMEOUT_SECONDS`：默认 `1800`。

`config.local.json` 使用独立的 `agentOs` 配置块：

```json
{
  "agentOs": {
    "baseUrl": "http://localhost:8000",
    "timeoutSeconds": 180,
    "maxAttempts": 3,
    "auth": {
      "cookie": "",
      "headerName": "",
      "headerValue": ""
    }
  },
  "tenderInterpretation": {
    "parseWaitTimeoutSeconds": 1800
  }
}
```

`config.local.json` 不进入版本控制。仓库可提供不含凭据的示例文件。

为保持应用在未配置 Agent OS 时仍可启动，`AGENT_OS_BASE_URL` 在首次实际调用客户端时
校验；缺失时该次解读明确失败。

## 错误处理与重试

可重试错误：

- 连接建立失败。
- 请求超时。
- HTTP 429、502、503、504。

上述错误最多尝试 `AGENT_OS_MAX_ATTEMPTS` 次，并采用有上限的短暂指数退避。

不可重试错误：

- 其他 HTTP 4xx。
- 响应不是 JSON 对象。
- 缺少 `report_markdown`。
- `report_markdown` 不是字符串或内容为空。

达到最大尝试次数或发生不可重试错误后，异常交给现有调度器处理，任务变为
`failed`，且不进入诊断。错误信息应包含应用名、错误类别和 HTTP 状态等诊断信息，
但不得包含鉴权值或招标文件全文。

由于 Agent OS 接口没有声明幂等键，重试可能造成重复推理计算；该应用只生成结果，
没有外部写操作，因此接受这一成本。后续若应用增加副作用，必须重新评估重试策略。

## 停止语义

等待解析期间，内容提供器应周期性检查停止标记，以便快速退出。

Agent OS 请求发出后，本项目无法保证终止服务端已经开始的推理。现有停止接口可能先
把任务标记为 `stopped`；当迟到响应返回时，调度器必须重新检查状态并丢弃结果：

- 不保存解读报告。
- 不覆盖 `stopped` 状态。
- 不进入诊断。
- 不把用户停止记录为调用失败。

## 测试策略

### AgentOSClient 单元测试

- URL 拼接和 `appName + input` 请求结构。
- 环境变量优先、JSON 配置回退。
- Header 和 Cookie 鉴权。
- 生产响应不使用测试态 envelope。
- 超时、连接错误和指定状态码的重试。
- 不可重试的 4xx、无效 JSON 和非对象响应。
- 日志与异常不泄露输入全文或鉴权信息。

### TenderContentProvider 单元测试

- 等待 `pending` 和 `running`。
- 接受 `succeeded` 与 `partial`。
- 从真实 `md_path` 读取完整内容。
- 解析失败、路径缺失、空文件和等待超时。
- 等待过程中停止任务。

### AgentOSInterpretationAgent 单元测试

- 精确映射 `tender_text`、`project_background` 和
  `interpretation_requirements`。
- 显式传入 `tender_doc_interpreter_app`。
- 从顶层 `report_markdown` 构造 `InterpretationResult`。
- 拒绝缺失或空报告。

### Scheduler 集成测试

- 顺序为“解析完成、智能体解读、诊断”。
- 智能体失败后任务为 `failed`。
- 用户停止后忽略迟到响应。
- 现有 Markdown/HTML 保存和下载流程保持可用。

默认自动化测试使用 Mock HTTP transport，不依赖真实 Agent OS。允许提供由明确环境
开关启用的手动联调测试，但不得纳入默认测试套件。

## 范围边界

本次不实现：

- `/v1/apps/chat` 和 `/v1/apps/aichat`。
- 测试态 `/api/v1/runtime/*`。
- 按解读要求检索招标文件片段。
- 结构化输出字段的数据库持久化。
- 数据库表结构、前端或诊断引擎改造。
- Agent OS 失败后的 Mock 降级。

## 验收标准

1. 招标文件解读使用现有解析流程生成的真实 Markdown 全文。
2. 生产请求命中 `/v1/apps/invoke`，并显式传入
   `tender_doc_interpreter_app`。
3. 输入字段与应用配置完全一致。
4. `report_markdown` 沿用现有报告保存和展示链路。
5. 解析或智能体失败时任务失败且不进入诊断。
6. 用户停止后不会保存迟到结果或恢复任务状态。
7. 通用客户端不绑定任何单一业务应用名。
8. 默认自动化测试不依赖外部 Agent OS。
