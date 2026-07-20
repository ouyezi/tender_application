# 招标文件解读智能体提示词优化设计

## 背景与问题

`Agent OS interpretation response for app 'tender_doc_interpreter_app' has invalid field 'report_markdown'` 来自后端适配器校验：Agent OS 调用成功返回后，`report_markdown` 缺失、非字符串或为空。

**已观测现象：** 模型直接输出 Markdown 报告正文，未包在 `outputSchema` 要求的 JSON 对象内（无 `report_markdown` 字段）。

**根因分析：**

1. 当前 `systemPrompt` 输出约束偏弱，缺少与同项目其他 Agent（`tender_checklist_generator`、`tender_batch_diagnosis`）一致的编号式字段规则。
2. 输出负担重：10 个 schema 字段 + `report_markdown` 需与结构化内容一致，模型倾向「写报告」而忽略 JSON envelope。
3. `outputSchema` 中 `report_markdown` 排在首位，与「先结构化、后汇总 Markdown」的自然顺序相反，加剧先输出 Markdown 正文的问题。

**约束：**

- 必须保留全部 9 个结构化输出字段（`project_basic_info` 等），供其他链路或后续使用。
- 后端当前仅消费 `report_markdown`；本次**不修改后端**，仅优化 Agent 配置（提示词 + schema 字段顺序）。
- 不新增 `schema_version`（保持现有 10 字段形状，避免破坏已有解析约定）。

## 目标

1. 消除「直接输出 Markdown 正文、无 JSON 包装」的行为。
2. 保证 `report_markdown` 为非空字符串。
3. 稳定产出全部 required 结构化字段；缺项用 `"未找到"` / `[]` / `false` 填充。
4. 与项目内其他 Agent 的提示词风格对齐。

## 方案选择

| 方案 | 说明 | 结论 |
|------|------|------|
| A | 强化提示词 + 逐字段约束 | **采用** |
| B | 叠加 JSON 骨架 Few-shot 示例 | **采用** |
| C | 后端容错（纯字符串响应映射为 report_markdown） | **暂不采用**；若发布后仍偶发可另开任务 |

## 变更范围

### 修改文件

- `docs/agents_config/tender_doc_interpreter.json`（本地工作副本，发布后同步）
- Agent OS 远端：`tender_doc_interpreter` 智能体 draft → validate → publish

### 不修改

- `backend/app/engine/interpretation_agent_os.py`
- 输入 schema（`tender_text` / `project_background` / `interpretation_requirements`）
- 结构化字段的名称、类型、required 标记

## Schema 变更

### 唯一 structural 调整

将 `outputSchema` 中 `report_markdown` 从**第一项**移至**最后一项**，与其生成顺序一致。

字段列表（最终顺序）：

1. `project_basic_info` (object, required)
2. `bidder_qualifications` (array, required)
3. `rejection_clauses` (array, required)
4. `evaluation_rules` (object, required)
5. `procurement_requirements` (array, required)
6. `contract_highlights` (array, required)
7. `bid_document_structure` (array, required)
8. `risks_and_notes` (array, optional)
9. `report_markdown` (string, required)

不新增 `schema_version`。

## 提示词设计

### 结构（五段式）

```
1. 【角色】招标文件解读专家

2. 【硬性输出约束】（置顶）
   - 必须且仅输出符合 outputSchema 的单一 JSON 对象
   - 禁止在 JSON 外输出 Markdown、说明文字、```json 代码块
   - report_markdown 是 JSON 内的 string 字段，Markdown 换行写在字符串值内

3. 【输入】
   {{tender_text}} / {{project_background}} / {{interpretation_requirements}}
   （背景与额外要求仅作补充；冲突以招标原文为准；可选字段为空则忽略）

4. 【提取任务】（现有 7 项业务要求，保持不变）

5. 【结构化字段规则】（编号 0–10，见下节）

6. 【生成顺序】
   先 project_basic_info → … → risks_and_notes → 最后 report_markdown

7. 【JSON 格式示意】（精简骨架，≤15 行）

8. 【收尾重申】严格 JSON，禁止额外文字
```

### 逐字段规则（写入 prompt）

**规则 0 — 全局**

- 顶层键只能是 outputSchema 定义的字段；禁止额外顶层键。
- 禁止把 Markdown 作为整个响应体；必须包在 JSON 的 `report_markdown` 字符串内。

**规则 1 — `project_basic_info`**

- 必为 object，含全部 9 子键：`project_name`、`project_code`、`tenderer`、`agency`、`budget_or_control_price`、`location`、`period`、`key_deadlines`、`other`。
- 子字段缺失填 `"未找到"`。

**规则 2 — `bidder_qualifications`**

- 必为 array；无则 `[]`。
- 每项必含 `requirement` (string)、`is_substantive` (boolean，禁止字符串 `"true"`/`"false"`)。
- `source_ref` 无则 `"未找到"`。

**规则 3 — `rejection_clauses`**

- 必为 array；无则 `[]`。
- 每项必含 `clause` (string)。
- `category` 建议：`形式` | `实质` | `其他`；无法判断填 `"其他"`。
- `source_ref` 无则 `"未找到"`。

**规则 4 — `evaluation_rules`**

- 必为 object，含 `method`、`score_weights`、`scoring_criteria`、`notes`；缺失填 `"未找到"`。

**规则 5 — `procurement_requirements`**

- 必为 array；无则 `[]`。
- 每项必含 `item` (string)、`is_mandatory_star` (boolean)。
- 招标原文标 ★ 的条款必须 `is_mandatory_star: true`。

**规则 6 — `contract_highlights`**

- 必为 string 数组；无则 `[]`；元素为 plain string。

**规则 7 — `bid_document_structure`**

- 必为 string 数组，按章/分册/附件顺序；无则 `[]`。

**规则 8 — `risks_and_notes`**

- optional；无则 `[]` 或省略。

**规则 9 — `report_markdown`（最后生成）**

- 必为非空 string，长度 ≥ 200 字符。
- 固定章节：

  ```markdown
  # 招标文件解读报告
  ## 一、项目基础信息
  ## 二、投标人资格要求
  ## 三、废标/否决条款
  ## 四、评标评分细则
  ## 五、采购需求
  ## 六、合同要点
  ## 七、投标文件格式组成
  ## 八、风险提示与编制注意事项
  ```

- 内容与规则 1–8 一致；不得与结构化字段矛盾。

**规则 10 — 生成顺序**

`project_basic_info` → `bidder_qualifications` → `rejection_clauses` → `evaluation_rules` → `procurement_requirements` → `contract_highlights` → `bid_document_structure` → `risks_and_notes` → **`report_markdown`**

### JSON 骨架示例（Few-shot）

置于 prompt 末尾，标注「格式示意，内容须替换为真实提取结果」：

```json
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
```

## 发布与验证（第三节）

### 发布流程

使用 `agent-create-publish` skill **update** 模式：

1. 基于 `docs/agents_config/tender_doc_interpreter.json` 定位 `agentId` / `enName`。
2. PATCH draft：更新 `io.outputSchema`（字段顺序）+ `prompt.systemPrompt`（新五段式提示词）。
3. validate → publish 智能体。
4. 应用 `tender_doc_interpreter_app` 已绑定 `latest`，发布后自动生效。
5. 成功后将完整配置回写本地 JSON（`publishedVersion` 递增）。

### 验证清单

| # | 检查项 | 通过标准 |
|---|--------|----------|
| 1 | JSON envelope | 响应为 object，非裸 Markdown 字符串 |
| 2 | `report_markdown` | 存在、为 non-empty string |
| 3 | 结构化字段齐全 | 全部 required 字段存在且类型正确 |
| 4 | 空数组默认 | 无资格/废标等场景输出 `[]` 而非省略 |
| 5 | boolean 类型 | `is_substantive` / `is_mandatory_star` 为 JSON boolean |
| 6 | 后端链路 | 解读任务 `interpreting` 阶段成功，Tab 展示 Markdown |
| 7 | 回归 | 现有 `backend/tests/test_interpretation_agent_os.py` 仍绿（无后端改动，应无影响） |

### 测试方式

1. **Agent OS 直调：** `POST /v1/apps/invoke`，`appName=tender_doc_interpreter_app`，传入短样本 `tender_text`（≥500 字真实招标片段）。
2. **端到端：** 上传测试招标文件，等待解读阶段完成，确认任务详情「解读报告」Tab 有内容且无 `invalid field 'report_markdown'` 错误。

### 成功标准

- 连续 3 次 invoke（含 1 份较长招标正文）均返回合法 JSON，`report_markdown` 非空。
- 不再出现「响应为裸 Markdown、无 JSON 包装」的情况。

### 回滚

- Agent OS 管理台回退至上一 published version，或重新 publish 旧版 draft。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 提示词过长占 token | 骨架示例控制在 ≤15 行；业务任务段保持简洁 |
| 长文档输出截断导致 JSON 不完整 | 监控 `timeoutMs`（当前 180s）；若仍截断，后续考虑后端容错（方案 C）或分片解读 |
| 结构化字段与 report_markdown 不一致 | 规则 10 强制最后生成 report_markdown；章节标题固定 |

## 不在本次范围

- 后端 `interpretation_agent_os.py` 容错解析
- 简化 outputSchema（删除结构化字段）
- 新增 `schema_version` 字段
- 修改模型 / temperature / thinking 配置
