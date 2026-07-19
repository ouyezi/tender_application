# 真实分类批诊断与 API E2E 验收设计

## 目标

1. **真实批诊断**：生产路径用 Agent OS 分类批诊断引擎替换 `MockBatchDiagnosisEngine`，对 `diagnosis_mode=file` 的检查项给出真实合规结论。
2. **索引门闩**：存在 file 项时，诊断前必须等待标书知识索引 `IndexJob.status == ready`。
3. **Agent 发布**：新建并发布 `tender_batch_diagnosis` / `tender_batch_diagnosis_app`，契约落盘 `docs/agents_config/`。
4. **可重复验收**：提供脚本化 API E2E，默认使用本机
   `uploads/T-20260716-005/{tender,bid}.docx`（标书约 1GB，需长超时）。

## 已确认决策

| 决策点 | 选择 |
|---|---|
| 范围 | 真实诊断引擎 + 索引门闩 + Agent 发布 + API E2E |
| 引擎形态 | 分类批诊断（沿用 `BatchDiagnosisEngine.diagnose_category`） |
| Agent 接入 | 新建 Agent OS 应用（create-publish 全流程在范围内） |
| 索引门闩 | 硬等待标书 IndexJob `ready`；失败/超时 → 任务失败 |
| 门闩条件 | 仅当存在至少一条 `diagnosis_mode=file` 时才 wait |
| Mock | 生产固定真实引擎；Mock 仅单测 monkeypatch，无配置回退 |
| 验收 | 脚本化 API E2E；样例路径本机传入/默认，不入库 |
| 样例文件 | `uploads/T-20260716-005/tender.docx` + `bid.docx`（接受长耗时） |

## 背景与现状

主链路已接通：上传 → 解析/索引 → Agent OS 解读 → Agent OS 检查项 → **Mock 批诊断** → 报告。

`docs/superpowers/specs/2026-07-19-offline-diagnosis-and-real-interpretation-design.md` 明确将「真实文件诊断引擎」列为另做；offline 分流与真实解读已落地。本次补齐该缺口。

阻碍真 E2E 的核心：`scheduler._run_diagnosis_phase` 固定使用 `MockBatchDiagnosisEngine`；诊断不等待标书索引就绪。

## 方案选择

采用**分类批诊断 Agent OS**（方案 A）：

- 每个分类一次 invoke，输入 category + file items + retrieved_chunks。
- 与现有调度器、检索、`assert_batch_complete` 协议一致，改动面最小。

未采用：

- 逐项诊断：调用次数与总耗时对大标书不友好。
- 两阶段抽证据再判定：契约与延迟翻倍，超出本期范围。

## 架构与流水线

```text
创建任务(上传招标+标书)
  → parse + index（招标/标书）
  → interpreting（已有 Agent OS）
  → generating_checklist（已有 Agent OS）
  → diagnosing
       ├─ 若存在 file 项：wait_for_bid_index_ready
       ├─ offline 项：直写 manual_required（不变）
       └─ file 项：retrieve → AgentOSBatchDiagnosisEngine.diagnose_category
  → completed（报告模板不变）
```

### 边界

- 不改检查项生成协议、不改报告模板结构。
- 不引入 Mock 回退或 `DIAGNOSIS_ENGINE` 生产开关。
- E2E 不把 1GB 样例提交进 git。

## 组件

| 组件 | 职责 |
|---|---|
| `AgentOSBatchDiagnosisEngine` | 实现 `BatchDiagnosisEngine`；组装输入 → `AgentOSClient.invoke` → 解析校验 → `list[BatchItemResult]` |
| `wait_for_bid_index_ready` | 轮询任务 `bid_file_id` 对应 `IndexJob`；ready 放行；failed/超时抛错 |
| `scheduler._run_diagnosis_phase` | 条件性索引门闩；生产引擎改为 Agent OS；offline 分流不变 |
| `docs/agents_config/tender_batch_diagnosis.json` | create-publish 后的工作副本 |
| `scripts/e2e_diagnosis_flow.py` | 真实文件 API 验收脚本 |

命名：

- agent `enName`: `tender_batch_diagnosis`
- appName: `tender_batch_diagnosis_app`

## Agent 契约

### 输入（sync `/v1/apps/invoke`）

三个必填 string 字段：

1. `system_instructions`：判定规则与输出约束（本地组装）。
2. `category_payload`：当前分类 + 本批 file 检查项 JSON（含 id、title、requirement、technique、importance、compliance_rules、consequence_rules、expected_evidence 等）。
3. `retrieved_chunks`：检索块 JSON 列表（`chunk_id` / `text` / `location`）。

空检索仍调用 Agent（由模型倾向输出 `insufficient_evidence`），不在本地短路。

### 输出

```json
{
  "schema_version": "1",
  "results": [
    {
      "checklist_item_id": "...",
      "compliance": "satisfied|violated|cannot_satisfy|insufficient_evidence",
      "consequence_tags": ["no_score|bid_unusable|score_risk|general_risk"],
      "evidence": "...",
      "suggestion": "...",
      "description": "..."
    }
  ]
}
```

校验：

- `assert_batch_complete`：结果 ID 集合必须与本批 file items 完全一致。
- `compliance` / `consequence_tags` 枚举非法 → 整批失败。
- 解析失败或 Agent OS 调用失败 → 任务 `failed`，`failure_stage=diagnosing`，不回退 Mock。

### 发布

实现阶段使用项目内 `agent-create-publish` skill：草案确认 → 创建/发布 → 落盘
`docs/agents_config/tender_batch_diagnosis.json`。后端常量 `appName` 与落盘一致。
应用 `timeoutMs` 建议 ≥ 180000。

## 索引门闩

- **时机**：进入 `diagnosing` 后、分类循环前，扫描全量检查项一次；若存在任意 `file` 项则 wait；全部 `offline` 则跳过 wait 与引擎调用。
- **对象**：任务标书 `bid_file_id` 的 `IndexJob`（不是招标）。若 `bid_file_id` 缺失 → 立即失败。
- **成功**：该 job `status == "ready"`。
- **失败**：该 job `status == "failed"`，或等待超时。
- **轮询**：`queued` / `running` / `partial` / 尚无 job → 继续等待（直至超时）。

## 错误处理

| 场景 | 行为 |
|---|---|
| 标书 IndexJob `failed` | 任务 `failed`，`failure_stage=diagnosing`，错误含索引失败信息 |
| 索引等待超时 | 同上，错误码语义 `bid_index_timeout` |
| Agent OS 未配置 / 调用失败 | 任务失败，不回退 Mock |
| 输出缺项/重复/枚举非法 | 该分类整批失败 → 任务失败 |
| 暂停 / 停止 | 保持现有调度语义 |

## 配置

| 键 | 默认 | 说明 |
|---|---|---|
| `BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS` 或 `config.local.json` → `batchDiagnosis.indexWaitTimeoutSeconds` | `7200` | 适配大标书索引 |
| `BATCH_DIAGNOSIS_INDEX_POLL_SECONDS` | 生产 `2.0`；测试可调小 | 轮询间隔 |
| `agentOs.*` | 沿用现有 | 解读 / 检查项 / 诊断共用 |

`config.local.json.example` 增加 `batchDiagnosis.indexWaitTimeoutSeconds` 示例（无凭据）。

## 测试

### 单元 / 集成（pytest，默认 stub Agent OS）

- `AgentOSBatchDiagnosisEngine`：合法 payload 映射；缺项 / 非法枚举抛错。
- `wait_for_bid_index_ready`：ready / failed / timeout；调度器在无 file 项时跳过 wait。
- scheduler：monkeypatch 引擎 + 假索引就绪；生产路径不再依赖 Mock 结论。
- 回归：offline 分流、暂停/停止、解读与检查项既有用例保持绿。

### API E2E 脚本

路径：`scripts/e2e_diagnosis_flow.py`。

前置：`startup.py` 已启动；`config.local.json` 可用；诊断 Agent 已发布。

参数：

- `--tender` / `--bid`（默认 `uploads/T-20260716-005/tender.docx` 与 `bid.docx`）
- `--base-url`（默认 `http://127.0.0.1:8888`）
- `--timeout-seconds`（默认 `14400`）

步骤：`POST /api/tasks` → 轮询 `GET /api/tasks/{id}` → 拉取 checklist / 报告相关信息。

成功断言（最小集）：

1. 终态 `completed`
2. 检查项报告存在且至少 1 条 item
3. 若存在 file 项：结果文本不含 `mock evidence for checklist item`
4. 报告可访问（detail 含报告路径或下载成功）

失败时打印 `status` / `failure_stage` / `error_message`，非 0 退出。

### 实现后验收顺序

1. create-publish 诊断 Agent 并落盘  
2. 单测全绿  
3. 启动服务并跑 E2E（接受数小时级耗时）  
4. 失败则按 `failure_stage` 定位  

## 明确不做

- 逐项诊断引擎或两阶段证据抽取流水线
- 生产 Mock 回退 / `DIAGNOSIS_ENGINE` 开关
- 把大样例文件提交进 git
- 浏览器 UI 自动化
- 改检查项生成或报告模板结构

## 与既有文档关系

- 承接并完成
  `2026-07-19-offline-diagnosis-and-real-interpretation-design.md` 中「真实文件诊断引擎另做」。
- 批诊断协议沿用
  `2026-07-17-tender-checklist-generation-design.md` 的 `BatchDiagnosisEngine`。
- 检索仍由 `WorkspaceRetrievalProvider` 提供；本期不改检索协议。
