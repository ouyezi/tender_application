---
name: agent-create-publish
description: Use when the user wants to create and publish an agent or application from a natural-language requirement against Agent OS / df-agent-os APIs, or asks to land an agent end-to-end via localhost agent management endpoints.
---

# Agent Create & Publish

## Overview

根据用户自然语言需求，生成一份完整「发布草案」→ **一次确认** → 按序调用可配置 HTTP API，完成智能体创建、配置、校验、发布，并创建与发布应用。

**硬门禁：** 用户确认草案之前，禁止调用任何写接口（POST create / PATCH draft / publish）。Step 0 仅允许只读调用（如模型列表）。

## Install / Portability

1. 将本目录 `agent-create-publish/` 复制到目标项目的 `.cursor/skills/` 或 `.claude/skills/`
2. 复制 `config.example.json` → `config.local.json`，按需改 `baseUrl` / 鉴权
3. 若后端路径或字段不同，直接改本文件中的 API 表与请求体模板
4. 将 `config.local.json` 加入目标项目 `.gitignore`

## Config

加载顺序：

1. 同目录 `config.local.json`（不存在则提示从 `config.example.json` 复制，**停止，不写接口**）
2. 环境变量覆盖：
   - `AGENT_OS_BASE_URL` → `baseUrl`
   - `AGENT_OS_AUTH_COOKIE` → `auth.cookie`
   - `AGENT_OS_AUTH_HEADER_NAME` → `auth.headerName`
   - `AGENT_OS_AUTH_HEADER_VALUE` → `auth.headerValue`
3. `baseUrl` 缺省：`http://localhost:8000`

请求：打到 `{baseUrl}`；若配置了 cookie / header 则带上。  
响应 envelope：`{ "code": 0, "data": ..., "message": "..." }`，仅 `code === 0` 视为成功。

### Shell 变量（curl 示例共用）

Step 0 及后续所有 curl 示例使用 `$BASE_URL` 与 `"${AUTH_ARGS[@]}"`。从上述 config 解析后 export：

- **`BASE_URL`**：取 `baseUrl`（经环境变量覆盖后），缺省 `http://localhost:8000`
- **`AUTH_ARGS`**：由 `auth` 构造为 **bash 数组**，供 curl 展开：
  - 若配置了 cookie：`AUTH_ARGS=(-H "Cookie: <cookie>")`
  - 若配置了 headerName + headerValue：`AUTH_ARGS=(-H "$headerName: $headerValue")`
  - 若两者均未配置：`AUTH_ARGS=()`
  - 若 cookie 与 header 同时存在：合并两个 `-H` 元素

> 鉴权开启时必须用 bash 数组 + `"${AUTH_ARGS[@]}"`，不要用带引号的字符串变量。

示例：

```bash
# cookie
export BASE_URL="http://localhost:8000"
AUTH_ARGS=(-H "Cookie: JSESSIONID=abc123")
```

```bash
# header
export BASE_URL="https://agent-os.example.com"
AUTH_ARGS=(-H "Authorization: Bearer token123")
```

```bash
# empty (no auth)
export BASE_URL="http://localhost:8000"
AUTH_ARGS=()
```

## When to Use

- 用户用自然语言描述要做一个智能体/应用，并希望创建且发布
- 用户明确要求对接 Agent OS（本仓库默认端口 8000）完成智能体落地

**When NOT to use**

- 只改已有智能体文案/配置、不涉及新建发布
- 只要设计提示词、不调用管理 API
- 需要封装脚本或批量迁移（本 skill 是交互式单次流程）

## HARD GATE

```
需求 → (可选一轮补充) → 只读拉模型列表 → 输出发布草案 → STOP
用户确认/修改并确认 → Step 1–7 连续执行
```

确认前调用写接口 = 违规。

## Step 0 — 草案（一次确认）

**硬前置：** 自然语言需求。  
若过短、无法推断「做什么 / 给谁用 / 期望产出」→ **只追问一轮**，然后仍输出完整草案。

**只读允许：**

```bash
curl -sS -X POST "$BASE_URL/api/v1/models/list" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{"page":1,"pageSize":100}'
```

列表失败时：草案模型区标「待确认」，提示服务可用或手填 `modelId`；仍可一次确认后执行。

### 草案模板（必须全部填满后展示）

```markdown
## 发布草案（请确认）

### 风险 / 待确认
- ...

### 智能体
- zhName:
- enName:   # snake_case
- description:

### IO
- formatInput / formatOutput:
- inputSchema:   # SchemaField[]：id,name,type,required,children?,itemType?
- outputSchema:
- 理由:

### 提示词
- systemPrompt:   # 角色、边界、输入用法、输出与 schema 对齐
- initialMessages: [] 或简要说明

### 模型
- modelId:
- temperature:
- thinking:
- 选型理由:

### Runtime（智能体）
- streaming / multiTurn / timeoutMs / retryCount / showThinking / sandboxEnabled:

### 应用
- name / enName:
- mode: chat | api
- chatConfig 或 apiConfig:
- concurrency / timeoutMs:
- agentVersionRef: { "publishMode": "latest" }

确认后我将连续执行创建→配置→校验→发布智能体→创建并发布应用。
请回复「确认」或给出修改点。
```

### 推断默认值

| 场景 | 倾向 |
|------|------|
| 对话 / 客服 / 多轮 | `mode=chat`；`runtime.multiTurn=true`；`chatConfig.stream` 与 `runtime.streaming` 默认 `true` |
| 结构化抽取 / 工具式调用 | `mode=api`；`formatInput`/`formatOutput=true`；`apiConfig.syncType=sync`（除非明确异步/回调） |
| 应用并发 | `concurrency=10`，`timeoutMs=30000` |
| 温度 | 创作 ~0.8；抽取/分类 ~0.2–0.4；否则 0.7 |
| 技能 | 默认不挂 `skillIds`（用户点名再加） |
| enName | snake_case；冲突则后缀重试 1 次 |

## Steps 1–7 — 确认后执行

先按 **Config → Shell 变量** 设置 `BASE_URL` / `AUTH_ARGS`。推荐 **一次完整 draft PATCH**（合并 2–5），逻辑顺序仍按 IO → 提示词 → 模型 → runtime 决策。

### 1) 创建智能体

```bash
curl -sS -X POST "$BASE_URL/api/v1/agents" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{"zhName":"...","enName":"...","description":"..."}'
```

记录 `data.id` → `AGENT_ID`。`enName` 冲突则改短后缀重试 **1 次**。

### 2–5) 写入 draft（可合并）

```bash
curl -sS -X PATCH "$BASE_URL/api/v1/agents/$AGENT_ID/draft" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{
    "prompt": {
      "systemPrompt": "...",
      "initialMessages": []
    },
    "model": {
      "modelId": "mdl_...",
      "backupModelId": "",
      "temperature": 0.7,
      "thinking": false
    },
    "io": {
      "formatInput": true,
      "inputSchema": [
        {
          "id": "f_input",
          "name": "query",
          "description": "用户问题",
          "type": "string",
          "required": true
        }
      ],
      "formatOutput": true,
      "outputSchema": [
        {
          "id": "f_output",
          "name": "answer",
          "description": "回答",
          "type": "string",
          "required": true
        }
      ]
    },
    "runtime": {
      "streaming": true,
      "retryCount": 0,
      "timeoutMs": 60000,
      "sandboxEnabled": false,
      "showThinking": false,
      "multiTurn": true
    },
    "skills": { "skillIds": [] }
  }'
```

纯闲聊可将 `formatInput`/`formatOutput` 设为 `false`，schema 用 `[]`。

若 Step 0 未拿到模型：此处再 `POST /api/v1/models/list` 选型，或使用用户确认的手填 `modelId`。

### 6) 校验

```bash
curl -sS -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/validate" \
  "${AUTH_ARGS[@]}"
```

- `data.valid === true` → 继续
- 否则按 `data.errors[].path/message` 修 draft，最多 **2 轮**；仍失败则停止并报告 `AGENT_ID`

### 7) 发布智能体 + 创建并发布应用

```bash
# 7a 发布智能体
curl -sS -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/publish" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{"message":"skill-create: <需求摘要>"}'
```

响应 `data` 为 `{ agent, version }`；`publishedVersion` 在 `data.agent.publishedVersion`。

```bash
# 7b 创建应用（chat 示例）
curl -sS -X POST "$BASE_URL/api/v1/applications" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{
    "name": "...",
    "enName": "..._app",
    "agentId": "'"$AGENT_ID"'",
    "agentVersionRef": { "publishMode": "latest" },
    "mode": "chat",
    "chatConfig": { "stream": true, "showToolCalls": false, "showThinking": false },
    "concurrency": 10,
    "timeoutMs": 30000
  }'

# 7b' api 模式时用 apiConfig，不要同时乱填 chatConfig：
# "mode": "api",
# "apiConfig": { "syncType": "sync" }

# 7c 发布应用
curl -sS -X POST "$BASE_URL/api/v1/applications/$APP_ID/publish" \
  "${AUTH_ARGS[@]}"
```

应用 `enName` 冲突同样后缀重试 1 次。

### 成功汇报（必须包含）

- `agentId`、zhName/enName、`publishedVersion`（来自 7a 响应 `data.agent.publishedVersion`）
- `appId`、应用 enName、`mode`
- 调用提示：
  - chat → `POST {baseUrl}/v1/apps/chat`（body 含 `appName`）
  - api → `POST {baseUrl}/v1/apps/invoke`（body 含 `appName`）

## API Quick Reference

| 步骤 | 方法 | 路径 |
|------|------|------|
| 列模型 | POST | `/api/v1/models/list` |
| 创建智能体 | POST | `/api/v1/agents` |
| 更新 draft | PATCH | `/api/v1/agents/{id}/draft` |
| 校验 | POST | `/api/v1/agents/{id}/validate` |
| 发布智能体 | POST | `/api/v1/agents/{id}/publish` |
| 创建应用 | POST | `/api/v1/applications` |
| 发布应用 | POST | `/api/v1/applications/{id}/publish` |

## Error Handling

| 情况 | 行为 |
|------|------|
| 无 `config.local.json` | 提示复制 example，停止 |
| 服务不可达 | 报告 baseUrl 与错误，停止 |
| enName 冲突 | 后缀重试 1 次；仍失败停止 |
| validate 失败 | 自动修最多 2 轮；仍失败展示错误与 agentId |
| 智能体已发布、应用失败 | **不回滚**；报告 agentId/version + 补创建应用请求草案 |
| 应用已建、publish 失败 | 报告 appId，提示手动 publish |
| 鉴权失败 | 检查 cookie/header 与服务端 `AUTH_ENABLED` |

## Common Mistakes

- 确认前就 POST/PATCH/publish
- 漏掉应用发布（只发了智能体）
- `mode=api` 却不填 `apiConfig`（或 chat 不填 `chatConfig`）
- 提示词与 outputSchema 不一致
- 模型列表为空仍强行 publish（应先停下或手填 modelId）
