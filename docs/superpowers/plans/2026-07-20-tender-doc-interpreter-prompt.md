# Tender Doc Interpreter Prompt Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化 `tender_doc_interpreter_app` 提示词与 outputSchema 字段顺序，使模型稳定返回 JSON envelope（含非空 `report_markdown`），消除裸 Markdown 响应导致的 `invalid field 'report_markdown'` 错误。

**Architecture:** 仅改 Agent OS 智能体配置（本地 `docs/agents_config/tender_doc_interpreter.json` → PATCH draft → validate → publish）。后端适配器不变。提示词对齐同项目 `tender_checklist_generator` 的编号式约束；`report_markdown` 移至 outputSchema 末位并最后生成。

**Tech Stack:** Agent OS HTTP API、`agent-create-publish` skill、JSON 配置工作副本。

**Spec:** `docs/superpowers/specs/2026-07-20-tender-doc-interpreter-prompt-design.md`

---

## File Structure

```text
docs/agents_config/
  tender_doc_interpreter.json     # MODIFY: reorder outputSchema, rewrite systemPrompt
docs/superpowers/specs/
  2026-07-20-tender-doc-interpreter-prompt-design.md  # reference only
```

无后端/前端代码变更。

---

### Task 1: 重排 outputSchema 并更新本地 JSON

**Files:**
- Modify: `docs/agents_config/tender_doc_interpreter.json`

- [ ] **Step 1: 打开 `io.outputSchema`，将 `report_markdown` 块剪切到数组末尾**

最终顺序必须为：

1. `project_basic_info`
2. `bidder_qualifications`
3. `rejection_clauses`
4. `evaluation_rules`
5. `procurement_requirements`
6. `contract_highlights`
7. `bid_document_structure`
8. `risks_and_notes`
9. `report_markdown`

除顺序外，每个字段的 `id`/`name`/`type`/`required`/`children`/`itemType` **不得改动**。

- [ ] **Step 2: 替换 `prompt.systemPrompt` 为以下内容（整段替换）**

```text
你是资深招标文件解读专家，服务投标编制、标书目录生成与投标合规诊断。

## 硬性输出约束

- 必须且仅输出一个符合 outputSchema 的 JSON 对象。
- 禁止在 JSON 外输出 Markdown 正文、说明文字或 ```json 代码块。
- report_markdown 是 JSON 内的 string 字段；Markdown 标题与换行写在字符串值内，不要把 Markdown 作为整个响应体。

## 输入内容

### 招标文件全文（主依据）
{{tender_text}}

### 项目背景与已知约束（可选补充）
{{project_background}}

### 额外解读要求（可选）
{{interpretation_requirements}}

背景与额外要求仅作补充，不得与招标原文冲突；冲突时以招标原文为准。若可选字段为空，忽略即可。

## 任务

通读招标全文，提取并输出以下内容（缺项标注「未找到」，不得臆造）：
1. 项目基础信息：名称、编号、招标人/代理、预算或控制价、地点、工期/服务期、关键时间节点等
2. 投标人资格要求：逐条列出，并明确区分实质性要求与非实质性要求，尽量标注条款出处
3. 全部废标/否决条款：尽量穷尽，含形式性废标与实质性否决，标注出处
4. 评标评分细则：评标方法类型、分项权重、评分标准摘要
5. 采购需求：逐条提取；凡招标文件标注★的强制条款必须 is_mandatory_star=true
6. 合同要点：付款、履约、验收、违约、质保等关键条款
7. 投标文件格式组成：按招标要求的章节/分册/附件结构列出，支撑目录生成

## 结构化字段规则

0. 顶层键只能是 outputSchema 定义的字段；禁止额外顶层键。
1. project_basic_info：必为 object，含全部 9 子键 project_name、project_code、tenderer、agency、budget_or_control_price、location、period、key_deadlines、other；子字段缺失填「未找到」。
2. bidder_qualifications：必为 array；无则 []；每项必含 requirement(string)、is_substantive(boolean，禁止用字符串 "true"/"false")；source_ref 无则「未找到」。
3. rejection_clauses：必为 array；无则 []；每项必含 clause(string)；category 建议 形式|实质|其他；source_ref 无则「未找到」。
4. evaluation_rules：必为 object，含 method、score_weights、scoring_criteria、notes；缺失填「未找到」。
5. procurement_requirements：必为 array；无则 []；每项必含 item(string)、is_mandatory_star(boolean)；★条款必须 is_mandatory_star=true。
6. contract_highlights：必为 string 数组；无则 []；元素为 plain string。
7. bid_document_structure：必为 string 数组，按章/分册/附件顺序；无则 []。
8. risks_and_notes：optional；无则 [] 或省略。
9. report_markdown：必为非空 string（长度≥200 字符）；章节固定为 # 招标文件解读报告、## 一、项目基础信息、## 二、投标人资格要求、## 三、废标/否决条款、## 四、评标评分细则、## 五、采购需求、## 六、合同要点、## 七、投标文件格式组成、## 八、风险提示与编制注意事项；内容与上述结构化字段一致，不得矛盾。

## 生成顺序

先填写 project_basic_info → bidder_qualifications → rejection_clauses → evaluation_rules → procurement_requirements → contract_highlights → bid_document_structure → risks_and_notes → 最后合成 report_markdown。

## JSON 格式示意（格式示意，内容须替换为真实提取结果）

{
  "project_basic_info": {
    "project_name": "示例项目",
    "project_code": "未找到",
    "tenderer": "未找到",
    "agency": "未找到",
    "budget_or_control_price": "未找到",
    "location": "未找到",
    "period": "未找到",
    "key_deadlines": "未找到",
    "other": "未找到"
  },
  "bidder_qualifications": [],
  "rejection_clauses": [],
  "evaluation_rules": {
    "method": "未找到",
    "score_weights": "未找到",
    "scoring_criteria": "未找到",
    "notes": "未找到"
  },
  "procurement_requirements": [],
  "contract_highlights": [],
  "bid_document_structure": [],
  "risks_and_notes": [],
  "report_markdown": "# 招标文件解读报告\n\n## 一、项目基础信息\n\n..."
}

## 收尾

严格输出符合 outputSchema 的 JSON 对象，不要输出额外说明文字。
```

- [ ] **Step 3: 校验 JSON 语法**

Run:

```bash
python3 -c "import json; json.load(open('docs/agents_config/tender_doc_interpreter.json'))"
```

Expected: 无输出（exit 0）

- [ ] **Step 4: 核对占位符**

确认 `systemPrompt` 含 `{{tender_text}}`、`{{project_background}}`、`{{interpretation_requirements}}` 各至少一次。

- [ ] **Step 5: Commit**

```bash
git add docs/agents_config/tender_doc_interpreter.json
git commit -m "chore: optimize tender doc interpreter prompt and schema order"
```

---

### Task 2: 发布到 Agent OS（update 模式）

**Files:**
- Read skill: `.cursor/skills/agent-create-publish/SKILL.md`
- Config: `.cursor/skills/agent-create-publish/config.local.json`（不存在则从 `config.example.json` 复制）

- [ ] **Step 1: 加载 agent-create-publish 配置**

确认 `config.local.json` 存在且 `baseUrl`/鉴权可用。设置 shell 变量：

```bash
export BASE_URL="http://localhost:8000"   # 或 config 中的 baseUrl
# AUTH_ARGS 按 skill 说明从 cookie/header 构造
```

- [ ] **Step 2: 生成更新草案并向用户确认（skill 硬门禁）**

相对当前 published 版本的 diff 摘要：

- `io.outputSchema`：`report_markdown` 移至末位
- `prompt.systemPrompt`：五段式 + 编号规则 + JSON 骨架

展示完整 `systemPrompt` 与 schema 顺序变更，**等待用户回复「确认」** 后再调用写接口。

- [ ] **Step 3: PATCH draft**

```bash
AGENT_ID="agt_19641944"   # 来自本地 JSON agent.id

curl -sS -X PATCH "$BASE_URL/api/v1/agents/$AGENT_ID/draft" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d @- <<'EOF'
{
  "io": { "...": "从 tender_doc_interpreter.json 的 io 段粘贴完整内容" },
  "prompt": {
    "systemPrompt": "...",
    "initialMessages": []
  }
}
EOF
```

实际执行时用 Task 1 更新后的 `io` + `prompt` 整段替换 `...`。

- [ ] **Step 4: Validate**

```bash
curl -sS -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/validate" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}"
```

Expected: `{"code":0,...}`

若失败：按 validate 错误修 draft，最多 2 轮。

- [ ] **Step 5: Publish agent**

```bash
curl -sS -X POST "$BASE_URL/api/v1/agents/$AGENT_ID/publish" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{"message":"skill-update: optimize interpreter prompt for JSON envelope compliance"}'
```

Expected: `data.agent.publishedVersion` 递增（当前为 3 → 应为 4）。

- [ ] **Step 6: 回写本地 JSON（U6）**

用 publish 响应 + draft 更新 `agent.publishedVersion`、`agent.publishedAt`，覆盖 `docs/agents_config/tender_doc_interpreter.json`。

- [ ] **Step 7: Commit 发布回写**

```bash
git add docs/agents_config/tender_doc_interpreter.json
git commit -m "chore: publish tender doc interpreter v4 prompt optimization"
```

---

### Task 3: Agent OS 直调验证

**Files:** 无代码变更；使用 curl 或现有 `config.local.json` 中的 Agent OS 地址。

- [ ] **Step 1: 准备短样本招标文本**

至少 500 字，含项目名称、资格要求、废标条款等段落（可用仓库内测试 upload 文件截取）。

- [ ] **Step 2: Invoke 三次**

```bash
curl -sS -X POST "$BASE_URL/v1/apps/invoke" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{
    "appName": "tender_doc_interpreter_app",
    "input": {
      "tender_text": "<样本正文>",
      "project_background": "",
      "interpretation_requirements": ""
    }
  }'
```

- [ ] **Step 3: 检查响应结构**

对每次响应验证：

| 检查 | 通过标准 |
|------|----------|
| 顶层类型 | object（非裸 Markdown 字符串） |
| `structuredOutput` | 若存在则检查其内；否则检查 payload 本身 |
| `report_markdown` | 存在、string、strip 后非空、含 `# 招标文件解读报告` |
| required 字段 | `project_basic_info`、`bidder_qualifications` 等齐全 |
| boolean | `is_substantive` / `is_mandatory_star` 为 JSON boolean |

- [ ] **Step 4: 记录结果**

三次均通过 → Task 3 完成。任一次失败 → 分析响应体（是否仍为裸 Markdown），回到 Task 1 微调 prompt 后重新 publish。

---

### Task 4: 后端回归（无改动预期）

**Files:**
- Test: `backend/tests/test_interpretation_agent_os.py`

- [ ] **Step 1: 运行现有测试**

```bash
cd backend && .venv/bin/pytest tests/test_interpretation_agent_os.py -v
```

Expected: 全部 PASS（后端未改，应无回归）

---

### Task 5: 端到端冒烟（可选，有运行中服务时）

- [ ] **Step 1: 创建解读任务并等待 `interpreting` 完成**

通过 UI 或 API 上传测试招标文件，观察任务状态。

- [ ] **Step 2: 确认解读报告 Tab**

任务详情「解读报告」展示 Markdown；日志/失败信息中**无** `invalid field 'report_markdown'`。

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| 重排 report_markdown 至 schema 末位 | Task 1 |
| 五段式提示词 + 编号规则 | Task 1 |
| JSON 骨架 Few-shot | Task 1（systemPrompt 内） |
| 不新增 schema_version | Task 1（不改字段集） |
| 不改后端 | Task 4 验证 |
| Agent OS publish | Task 2 |
| 三次 invoke 验证 | Task 3 |
| E2E 冒烟 | Task 5 |

## Rollback

Agent OS 管理台 re-publish 上一版本（v3），或将本地 JSON 回退到 `publishedVersion: 3` 的 git 提交后重新 publish。
