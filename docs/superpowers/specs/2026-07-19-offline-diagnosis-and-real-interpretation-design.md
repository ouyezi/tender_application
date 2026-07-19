# Offline 诊断跳过与真实解读接通设计

## 目标

修复并优化三条现状问题：

1. **打印 / 装订 / 密封盖章等线下要求**：仍生成并展示检查项，但不对投标文件做检索与文件诊断；结果记为「需线下核验」。
2. **解读报告内容来源**：当前 Tab 展示的是 `MockInterpretationAgent` 硬编码 Markdown，不是模型输出。本次接通真实 Agent OS 解读，展示 `report_markdown`。
3. **主链路 Mock 清点**（排除真实文件诊断引擎，该项另做）：解读仍是 Mock，需接通；检查项生成与检索侧 AI 已是真实 Agent OS；批诊断结论仍为 Mock（明确保留）。

## 已确认决策

| 决策点 | 选择 |
|---|---|
| offline 项处理 | 仍产出并展示；诊断阶段跳过文件检索/诊断；结果为 `manual_required`（需线下核验）并附说明 |
| 识别方式 | 检查项生成 Agent 标注 `diagnosis_mode`；不做关键词启发式 |
| 漏标默认 | 缺失 / 空 / 非法值 → `file` |
| 合规状态 | 新增专用值 `manual_required`（中文：需线下核验） |
| 解读 | 本次一并接通真实 Agent OS，失败不回退 Mock |
| 落地形态 | 双轨最小改动（scheduler 内分流）；不抽独立 Router 层 |

## 当前问题

### 解读

- 调度器固定使用 `MockInterpretationAgent`，未读招标解析 Markdown。
- `AgentOSClient` 已在 main（检查项 / 检索复用）；`AgentOSInterpretationAgent` 与解析等待尚未合入。
- 既有详细契约见
  `docs/superpowers/specs/2026-07-17-agent-os-tender-interpretation-design.md`。
  本次解读部分**采纳该设计的行为与契约**，并在本文件补充与 offline 改造的组合关系。

### 诊断项

- 检查项仅有 `content_source`（如何取正文），没有「是否应对文件做诊断」维度。
- `_run_diagnosis_phase` 对 category 内全部 items 一律检索 + `MockBatchDiagnosisEngine`。
- 打印 / 装订类项若进入真实诊断，会浪费检索并产生误导性合规结论。

## 方案选择

采用**方案 1：双轨最小改动**。

- 解读：按 2026-07-17 设计接通 `TenderContentProvider` + `AgentOSInterpretationAgent`。
- offline：在 `_run_diagnosis_phase` 按项分流，直写结果。

未采用：

- 统一 DiagnosisRouter 层：边界更清晰，但对当前范围偏重。
- 更细的 print / binding / seal 子类型：当前只需「不查文件 + 线下核验」，违反 YAGNI。

## 架构与流水线

流水线顺序不变：

```
interpreting（真实 Agent OS 解读）
  → generating_checklist（已有 Agent OS + diagnosis_mode）
  → diagnosing（offline 直写 / file 检索 + Mock 批诊断）
  → completed（模板诊断报告）
```

明确不做：真实文件诊断引擎、关键词启发式、print/binding 细分子类型、Mock 解读回退。

## 数据模型与 Agent 契约

### `diagnosis_mode`

| 字段 | 类型 | 取值 | 默认 |
|---|---|---|---|
| `diagnosis_mode` | string | `file` \| `offline` | `file` |

落点：

- DB：`checklist_items.diagnosis_mode`（迁移默认 `file`）
- `ChecklistItemDraft` / merge / API schema 同步透传
- 检查项生成 Agent（`tender_checklist_generator`）：output schema 与 system 规则增加该字段

Agent 标注规则（写入 system 指令）：

- 凡属打印、装订、密封、签字盖章等**无法靠投标文件电子正文核验**的要求 → `offline`
- 其余 → `file`
- 漏标或非法值：解析侧归一为 `file`，**不**整批失败

### 合规状态 `manual_required`

与现有 `satisfied` / `violated` / `cannot_satisfy` / `insufficient_evidence` 并列。

offline 直写 `DiagnosisResult`：

- `compliance_status` / `result` = `manual_required`
- `evidence` = `未检索文件（线下核验项）`
- `suggestion` = `该项属于打印/装订/密封等线下要求，需人工核验纸质或现场材料，系统不进行文件诊断`
- `consequence_tags`：若能从检查项 `consequence_rules` 解析则带上，否则 `[]`
- `description`：取检查项 `requirement`；若为空则用 `title`（与 Mock 批诊断对 description 的用法对齐）

### 解读 Agent 契约

沿用 2026-07-17 设计：

- 应用名：`tender_doc_interpreter_app`（适配器显式持有，不读全局应用名 env）
- 输入：`tender_text`、`project_background`、`interpretation_requirements`
- 输出：顶层非空字符串 `report_markdown`
- 先等待招标解析完成，读取真实 Markdown；不得用原始 `tender_path` 直接解读
- 失败 → 任务 `failed`，`failure_stage=interpreting`，不进入检查项生成，不回退 Mock
- 停止语义、重试、配置优先级见既有解读设计，本文件不重复展开

## 诊断分流

挂点：`scheduler._run_diagnosis_phase`。

对每个 category 的 `items`：

1. 拆成 `offline_items`（`diagnosis_mode == "offline"`）与 `file_items`（其余，含默认 `file`）。
2. **offline**：不调用检索、不调用 `MockBatchDiagnosisEngine`；按上节约定直写结果。
3. **file**：仅对 `file_items` 调用 `retrieve_for_category` + `diagnose_category`。若 `file_items` 为空，整类跳过检索与引擎。
4. 写库顺序保持检查项原有 sort；`progress_done` 按检查项总数累计（offline + file 均计已处理）。
5. `assert_batch_complete` 仅校验参与引擎的 `file_items`；offline 由直写路径保证条数一一对应。

边界：

| 场景 | 行为 |
|---|---|
| 整类全是 offline | 只直写，不触发检索 |
| 同 category 混有 file + offline | 分流后分别处理，写库保持原顺序 |
| 旧数据无该字段 | 迁移 / 默认 `file`，行为与现网一致 |

实现位置允许用小型私有辅助函数（如 `_write_offline_results`、`_split_by_diagnosis_mode`），但**不**引入独立 DiagnosisRouter 模块（留给真实诊断接入时再抽）。

## 展示

- 诊断结果表（`ResultTable`）与诊断报告 Markdown：`manual_required` →「需线下核验」。
- 检查项报告：对 `diagnosis_mode == offline` 的项展示简短「线下核验」标签；不做筛选或分组重构。
- 解读报告 Tab：交互不变；内容改为真实 `report_markdown`。

## 配置与发布

### 解读接线

- 调度器改为：等待解析 → `AgentOSInterpretationAgent.interpret(...)` → 现有 `save_interpret_reports`。
- 生产路径固定真实解读；`INTERPRETATION_AGENT` 若保留仅作兼容/文档，**不**提供 Mock 回退。
- 公共连接复用现有 `AGENT_OS_*` / `agentOs` 与
  `TENDER_PARSE_WAIT_TIMEOUT_SECONDS` / `tenderInterpretation.parseWaitTimeoutSeconds`
  （详见 2026-07-17 设计）。

### Agent 发布

- 更新并重新发布 `tender_checklist_generator`：output schema + system 规则增加 `diagnosis_mode`。
- `tender_doc_interpreter_app`：已发布则以
  `docs/agents_config/tender_doc_interpreter.json` 核对后只做调用接通。

## 错误处理

| 场景 | 行为 |
|---|---|
| 解读超时 / Agent 失败（不可重试或耗尽重试） | 任务 `failed`，不进入检查项生成 |
| 用户停止后迟到解读响应 | 丢弃结果，不覆盖 `stopped` |
| `diagnosis_mode` 缺失或非法 | 当作 `file`，不失败 |
| file 项检索/批诊断失败 | 保持现有 scheduler 失败语义（本次不改） |

## 测试策略

### `diagnosis_mode` 解析与落库

- 合法 `offline` / `file` 保留。
- 缺省、空、未知值 → `file`。
- API / 报告 JSON 透传该字段。

### 诊断分流（scheduler 或单元）

- offline 项不调用 retrieval / batch engine（可用 spy / monkeypatch 断言）。
- 结果为 `manual_required`，suggestion 含线下核验说明。
- 混合 category：file 仍走引擎；进度与条数正确。

### 解读

- 复用 / 补齐 2026-07-17 设计中的客户端、内容提供器、适配器、scheduler 集成测试。
- 默认测试注入 fake invoke，不依赖真实 Agent OS。
- 断言调度路径不再实例化 `MockInterpretationAgent` 作为生产默认。

### 前端

- `manual_required` 显示为「需线下核验」。

## 范围边界

本次实现：

- 真实解读接通（采纳 2026-07-17 行为契约）。
- `diagnosis_mode` 字段、Agent 配置更新与发布、解析默认 `file`。
- offline 诊断直写与展示标签。

本次不实现：

- 真实文件诊断引擎（批诊断结论可继续 Mock）。
- 关键词启发式识别 offline。
- print / binding / seal 等更细枚举。
- 独立 DiagnosisRouter 模块。
- Agent OS 解读失败后的 Mock 降级。

## 与既有设计的关系

- **解读细节**（客户端、等待解析、重试、停止语义、验收条）：以
  `2026-07-17-agent-os-tender-interpretation-design.md` 为准；若与本文冲突，以本文「已确认决策」与「范围边界」为准（例如：本次必须合入 main 调度路径）。
- **检查项生成 / 批诊断协议**：在
  `2026-07-17-tender-checklist-generation-design.md` 之上增量增加 `diagnosis_mode` 与分流，不推翻既有 category 批处理模型。

## 验收标准

1. 任务详情「解读报告」来自模型 `report_markdown`，不再是固定 Mock 模板文案。
2. 检查项可携带 `diagnosis_mode`；Agent 漏标时默认为 `file`。
3. `offline` 项出现在检查项中，诊断结果为「需线下核验」，且不对这些项发起检索与批诊断调用。
4. `file` 项行为与现网一致（检索 + Mock 批诊断）。
5. 解读失败时任务失败且不进入检查项生成；用户停止后不保存迟到解读结果。
6. 除「真实诊断」外，主任务链路中需调大模型且仍为未接线 Mock 的路径已消除（解读已接通）。
7. 默认自动化测试不依赖外部 Agent OS。
