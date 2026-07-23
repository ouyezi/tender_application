# 检查项生成 Schema v2 简化设计

**日期：** 2026-07-23  
**状态：** 已批准（brainstorming 确认）  
**范围：** 端到端简化检查项生成：固化分类、Agent 只输出 items、判定字段 Markdown 化、出处字段 Markdown 化  
**前置：**

- `2026-07-17-agent-os-checklist-generation-design.md`
- `2026-07-17-tender-checklist-generation-design.md`

---

## 1. 背景与问题

当前检查项生成存在以下痛点：

1. **Agent 输出 schema 不稳定**：模型常返回 nested 结构（`categories[].items`）、错误字段名（`category_name`）、或缺少顶层 `items`，导致 `parse_checklist_payload` 失败。
2. **结构化字段对模型过重**：`compliance_rules`、`consequence_rules` 要求 JSON 对象且键值类型严格；`source_references` 要求字符 offset，易触发 `coordinate_space is unsupported` 或 offset 校验失败。
3. **分类由模型生成**：动态 categories 增加输出复杂度，与产品固定的六类检查维度不一致。

### 1.1 目标

1. **固化 6 个检查分类**，由后端常量注入，Agent 不再生成 `categories`。
2. **Agent 最后一步只输出 `schema_version + items[]`**，结构扁平、字段少。
3. **`expected_evidence`、`compliance_rules`、`consequence_rules`、`source_citations` 改为 Markdown 字符串**，降低模型结构化输出失败率。
4. **端到端适配**：parser、validate、merge、DB/API、批诊断、offline 短路、前端展示。

### 1.2 成功标准

1. 新任务 checklist 生成使用 `schema_version: "2"`，Agent 响应可稳定解析。
2. API 返回的 `categories` 始终为固定六类；`items` 中判定字段为 Markdown 文本。
3. 批诊断可基于 Markdown 版 `compliance_rules` 正常判定；offline 项仍走 `manual_required` 短路。
4. 已有 v1 generation 只读可展示；重新生成走 v2。

### 1.3 本期不做

- 不新增第二个「合并专用」智能体（跨分片合并仍由后端 `merge_checklist_drafts` 完成）。
- 不改批诊断**结果**层的 JSON schema（`compliance`、`consequence_tags` 等仍结构化）。
- 不对历史 v1 数据做批量迁移 rewrite（仅兼容只读）。

---

## 2. 方案选择

### 2.1 已选方案：Schema v2 一刀切

- Agent 输出 `schema_version: "2"` + `items[]` only。
- 后端 `FIXED_CATEGORIES` 注入分类元数据。
- 判定与出处字段 Markdown 化；删除 offset 校验。
- 全链路一次性适配。

### 2.2 未选方案

| 方案 | 未选原因 |
|------|----------|
| v2 + 保留 `consequence_tags[]` | 与用户「全 Markdown 简化」目标不完全一致；改用 Markdown 首行标签约定 |
| Agent 简化 + Normalizer 隐式转换 | Agent 输出与入库数据不一致，调试困难 |

### 2.3 Brainstorming 决策摘要

| 决策项 | 选择 |
|--------|------|
| 范围 | 端到端（Agent + 后端 + 批诊断 + 前端） |
| 判定字段 | 三个独立 Markdown 字符串 |
| 出处 | 单个 `source_citations` Markdown（替代 `source_references`） |
| 分类 | Agent 不输出 categories；后端注入 |

---

## 3. 固定分类

后端常量 `FIXED_CATEGORIES`（`checklist_context.py` 或独立模块）：

| id | name | description（摘要） |
|----|------|---------------------|
| cat_001 | 废标红线 | 导致否决/不予受理的重大偏差 |
| cat_002 | 资质文件 | 资格证明文件与合规材料 |
| cat_003 | 格式要求 | 编制、签署、封装等形式要求 |
| cat_004 | 得分检查 | 影响评分的响应与填报项 |
| cat_005 | 风险检查 | 履约/一致性与潜在争议点 |
| cat_006 | 其他检查 | 未归入上述类别的必要检查项 |

每条固定分类还需填充（后端生成，Agent 不输出）：

- `retrieval_query`：按分类预设检索 query 模板
- `expected_locations`：默认 `[]` 或按分类预设
- `sort_order`：1–6

发布 checklist 时，`ChecklistCategory` 行始终来自 `FIXED_CATEGORIES`，不来自 Agent。

---

## 4. Agent 输出 Schema v2

### 4.1 顶层结构

```json
{
  "schema_version": "2",
  "items": [ ... ]
}
```

**禁止** Agent 输出：`categories`、顶层 `diagnosis_mode`、nested `categories[].items`。

### 4.2 items[] 字段

| 字段 | 类型 | required | 说明 |
|------|------|----------|------|
| id | string | 是 | 分片内 local id，如 `item_001` |
| category_id | string | 是 | 仅允许 `cat_001`～`cat_006` |
| title | string | 是 | 检查项标题 |
| requirement | string | 是 | 完整判断条件 |
| technique | string | 是 | 可执行检查方法 |
| importance | string | 是 | `high` \| `medium` \| `low` |
| diagnosis_mode | string | 是 | `file` \| `offline` |
| source_citations | string | 是 | Markdown，招标依据 |
| expected_evidence | string | 是 | Markdown，预期证据 |
| compliance_rules | string | 是 | Markdown，符合性判定 |
| consequence_rules | string | 是 | Markdown，后果说明（首行标签，见 4.3） |
| sort_order | number | 是 | 分片内排序，≥1 |

**不再要求 Agent 输出：**

- `retrieval_hints` → 后端从 `title` + `requirement` 自动生成
- `admin_config_refs` → 默认 `[]`
- `source_references` → 由 `source_citations` 替代

### 4.3 Markdown 字段约定

**source_citations**

```markdown
- 章节：第三章 评审办法 5.3
- 要点：未签字盖章属于重大偏差
```

**expected_evidence**

```markdown
- 投标函签章页
- 授权委托书原件或扫描件
```

**compliance_rules**

```markdown
## 满足
材料齐全且符合要求。

## 违反
缺少签字、盖章或两者不符。

## 不能满足
无

## 证据不足
电子版无法确认签章真实性，需现场核验。
```

- `file` 项：`## 证据不足` 写 `无`
- `offline` 项：`## 证据不足` 必填

**consequence_rules**

首行必须是机器可读标签（四选一），第二行起为说明：

```markdown
[bid_unusable]
未按采购文件规定签字盖章，响应文件将被否决。
```

标签枚举：`bid_unusable` | `score_risk` | `no_score` | `general_risk`

- 禁止使用 JSON 对象
- 禁止使用布尔值 `true`/`false`

---

## 5. 后端数据流

```
招标正文
  → split_tender_markdown（不变）
  → 每 segment 调用 Agent OS（输出 v2 items）
  → parse_checklist_payload_v2（校验 + 补 retrieval_hints）
  → merge_checklist_drafts（按 category_id 分桶，去重）
  → inject FIXED_CATEGORIES → ChecklistDraft
  → validate_draft_v2（无 offset 校验）
  → publish DB + JSON artifact
```

### 5.1 模块改动

| 模块 | 改动要点 |
|------|----------|
| `checklist_context.py` | `FIXED_CATEGORIES`；`CHECKLIST_SCHEMA_VERSION = "2"`；更新 `SYSTEM_INSTRUCTIONS` |
| `checklist_agent_os.py` | `parse_checklist_payload` 支持 v2；v1 可保留只读解析或移除 |
| `checklist_service.py` | `validate_draft` 分支 v2；删除 `_validate_source_reference`；Markdown 非空校验 |
| `checklist_merge.py` | 按 `category_id` 分桶；超 `MAX_ITEMS_PER_CATEGORY` 时按 title 前缀/首行分组 |
| `engine/base.py` | `ChecklistItemDraft` 字段类型：`source_citations: str`；三判定字段 `str` |
| `models.py` | 列语义变更：`source_references` 列存 `source_citations` 文本（或 rename migration） |
| `schemas.py` | API 响应类型同步 |
| `batch_diagnosis_context.py` | 判定规则改为阅读 Markdown `compliance_rules` |
| `scheduler.py` | offline：从 `consequence_rules` 首行 `[tag]` 解析 `consequence_tags` |
| `retrieval/*` | 继续读 `retrieval_hints`（后端生成，存储不变） |
| `ChecklistReport.jsx` | 判定/出处字段按 Markdown/纯文本展示，去掉 JSON pretty-print |

### 5.2 schema_version 与兼容

- 新 generation：`schema_version = "2"`
- 读取 v1 已发布 checklist：API 层按 generation 的 schema_version 格式化（v1 仍返回 list/dict 结构）
- 或统一 API 返回 string，v1 做 JSON→string 展示适配（实现时二选一，优先「按 version 分支」减少破坏）

### 5.3 offline 短路

`scheduler._offline_batch_result` 当前从 `consequence_rules` dict keys 取 tags。v2 改为：

```python
# 解析 consequence_rules 首行 [bid_unusable] 形式
tags = parse_consequence_tags_from_markdown(item["consequence_rules"])
```

解析失败时 `tags = []`，不影响 `manual_required` 主流程。

---

## 6. 批诊断适配

输入 `category_payload.items` 变化：

- `compliance_rules`、`consequence_rules`、`expected_evidence` 为 Markdown 字符串
- `source_citations` 替代 `source_references`

批诊断 Agent（`tender_batch_diagnosis_app`）**输出不变**：仍返回结构化 `compliance` + `consequence_tags`。

提示词调整要点：

1. 阅读检查项 Markdown 判定指南（`compliance_rules`）与 `requirement`。
2. 依据 `retrieved_chunks` 判定；不得臆造证据。
3. `consequence_tags` 可参考检查项 `consequence_rules` 首行标签。

---

## 7. Agent OS 配置

### 7.1 outputSchema 更新

`docs/agents_config/tender_checklist_generator.json` 的 `outputSchema` 同步为 v2 items-only 字段定义（实现阶段更新）。

### 7.2 最后一步提示词

Agent 内部工作流「第七步：诊断项汇总与标准化」使用附录 A 完整提示词（用户手动粘贴至 Agent OS，不走 API 替换）。

分片调用步骤（若仍逐步生成）的提示词亦应指向：最终汇总输出 v2 flat items，或各分片先产候选、第七步统一汇总为 v2。

---

## 8. 测试计划

1. **Parser**：v2 合法 payload；非法 category_id；Markdown 字段缺失；consequence 首行标签解析。
2. **Validate**：不再因 offset/coordinate_space 失败；固定分类注入完整。
3. **Merge**：跨分片去重；按 category_id 分桶；超上限分组。
4. **API**：GET checklist 返回 v2 字段形状。
5. **Batch diagnosis**：Markdown compliance 输入下 mock 判定通过。
6. **Offline**：`diagnosis_mode=offline` + `[bid_unusable]` 标签解析。
7. **Regression**：v1 已发布任务只读加载。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 批诊断读 Markdown 判定不稳定 | 提示词强调 `## 满足/违反` 结构；保留 requirement 作为主判定依据 |
| consequence 首行标签遗漏 | validate 警告或默认 `general_risk`；提示词强制首行格式 |
| 历史 v1 前端展示异常 | API 按 schema_version 分支渲染 |
| retrieval 质量下降 | 后端自动生成 retrieval_hints（title + requirement 分词） |

---

## 附录 A：最后一步完整提示词

（粘贴至 Agent OS「诊断项汇总与标准化」步骤）

```markdown
## 当前任务：诊断项汇总与标准化

综合前六步生成的检查项，输出最终诊断结果 JSON。

本步骤**不得新增**检查项，仅负责：汇总、去重、标准化、归类、输出。

---

## 固定分类（只能使用以下 category_id）

| category_id | 分类名 |
|-------------|--------|
| cat_001 | 废标红线 |
| cat_002 | 资质文件 |
| cat_003 | 格式要求 |
| cat_004 | 得分检查 |
| cat_005 | 风险检查 |
| cat_006 | 其他检查 |

**禁止输出 categories 数组。** 每条检查项通过 `category_id` 归入以上六类之一。

---

## 汇总原则

1. 相同检查项仅保留一条，合并 requirement、technique、source_citations 及出处。
2. `requirement` 完整表达判断条件；`technique` 形成可直接执行的检查方法（检查模块、内容、依据、比对方式）。
3. `importance` 保持前序结果；冲突时取最高（high > medium > low）。
4. `diagnosis_mode` 逐条设置：`file`（电子版可确认）或 `offline`（签章、密封、纸质、现场递交等）。
5. 合并所有招标出处到 `source_citations`。
6. 去重后按分类内逻辑顺序设置 `sort_order`（从 1 递增）。

---

## 输出 Schema（必须严格匹配）

只输出一个 JSON 对象，顶层**仅两个字段**：

```json
{
  "schema_version": "2",
  "items": []
}
```

### items[] 每条必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 如 item_001，全局唯一 |
| category_id | string | 只能是 cat_001～cat_006 |
| title | string | 检查项标题 |
| requirement | string | 判断条件 |
| technique | string | 检查方法 |
| importance | string | high / medium / low |
| diagnosis_mode | string | file / offline |
| source_citations | string | Markdown，招标依据 |
| expected_evidence | string | Markdown，预期证据 |
| compliance_rules | string | Markdown，符合性判定 |
| consequence_rules | string | Markdown，后果说明（见模板） |
| sort_order | number | 排序，从 1 开始 |

---

## Markdown 字段模板

### source_citations
```
- 章节：第三章 评审办法 5.3
- 要点：未签字盖章属于重大偏差
```

### expected_evidence
```
- 投标函签章页
- 授权委托书原件或扫描件
```

### compliance_rules
```
## 满足
材料齐全且符合要求。

## 违反
缺少签字、盖章或两者不符。

## 不能满足
（无则写「无」）

## 证据不足
（offline 项必填，如：电子版无法确认签章真实性，需现场核验；file 项写「无」）
```

### consequence_rules
首行必须是后果标签（四选一），第二行起写说明：
```
[bid_unusable]
未按采购文件规定签字盖章，响应文件将被否决。
```
标签：`bid_unusable`（废标）、`score_risk`（得分风险）、`no_score`（不得分）、`general_risk`（一般风险）

---

## 禁止事项

- 禁止输出 categories
- 禁止输出 source_references / coordinate_space / segment_index / start / end
- 禁止将 items 嵌套在 categories 内
- 禁止 compliance_rules、consequence_rules 使用 JSON 对象（必须是 Markdown 字符串）
- 禁止 consequence_rules 使用 true/false
- 禁止输出解释、Markdown 包裹、代码块外的任何文字

---

## 输出要求

- schema_version 固定为 "2"
- items 非空
- 所有 category_id 必须属于 cat_001～cat_006
- 仅输出合法 JSON，无其他内容
```

---

## 附录 B：实现任务清单（供 writing-plans 展开）

1. 常量与类型：`FIXED_CATEGORIES`、`ChecklistItemDraft` v2 字段
2. Parser + validate v2
3. Merge 按 category_id
4. DB/API schema 适配
5. 批诊断 + offline 解析
6. 前端展示
7. 测试与 `tender_checklist_generator.json` 快照更新
8. Agent OS 手动更新第七步提示词（附录 A）
