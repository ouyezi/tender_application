# E2E Diagnosis Run Findings — T-20260720-002

归档自真实文件 E2E run 证据包。完整 artifacts 见 `artifacts/e2e/T-20260720-002/`（本地 gitignored）。

**Run 结论：** interpreting 阶段失败（invalid `report_markdown`），未进入 checklist / diagnosis；observability 在失败时正常 flush。

---

## 1. Run 摘要

| 字段 | 值 |
|---|---|
| task_id | `T-20260720-002` |
| sample | `uploads/T-20260716-005`（tender.docx + bid.docx，bid ~1GB） |
| started_at | 2026-07-20T01:16:57.020596+00:00 |
| ended_at | 2026-07-20T01:17:27.908447+00:00 |
| final_status | `failed` |
| exit_code | 1 |
| failure_stage | `interpreting` |
| error_message | Agent OS interpretation response for app 'tender_doc_interpreter_app' has invalid field 'report_markdown' |
| artifacts | `artifacts/e2e/T-20260720-002/` |

**产出：** 无 `checklist.json`、`report.docx`（run 在 interpreting 前阶段失败）。

**产出抽检（`results_summary.json`）：** `item_count=0`，`result_count=0`，`mock_evidence_detected=false`。

---

## 2. 阶段耗时表

| 阶段 | 耗时 |
|---|---|
| upload | 5.1s |
| `interpreting` | 25.6s |
| `failed` | 0.1s |
| **total (from first status)** | **25.7s** |

未出现：`generating_checklist`、`diagnosing`、`completed`。

---

## 3. 问题清单

### P0 — interpreting 阶段失败，阻断整条诊断 E2E

- **现象：** `status=failed`，`failure_stage=interpreting`
- **错误：** Agent OS interpretation response for app `tender_doc_interpreter_app` has invalid field `report_markdown`
- **影响：** 未进入 checklist / diagnosis；全零产出
- **证据：** `artifacts/e2e/T-20260720-002/task_final.json`、`meta.json`、`timeline.json`
- **优先级：** P0

自动 skeleton 已捕获 P0；artifacts 在失败时 flush 正常。

### P1 — 完整 batch-diagnosis 路径未被执行

- **现象：** pipeline 在 interpreting 终止
- **影响：** 无法验证 `AgentOSBatchDiagnosisEngine`、index wait、无 mock results
- **证据：** `results_summary.json`、`timeline.json`、`index_status.json`
- **优先级：** P1（依赖 P0 修复后重跑）

### P2 — 运维：`tee` 管道可能掩盖非零退出码

- **现象：** `script | tee log` 的 pipeline exit 常来自 `tee`（0）
- **建议：** `set -o pipefail` 或 redirect 且保留脚本 exit code
- **优先级：** P2（observability/ops）

---

## 4. 优化建议

> 仅记录，不实施业务修复（scope A）。

1. **Agent OS 契约 vs 本地校验** — 调查 `tender_doc_interpreter_app` 输出与 validator 对顶层非空 `report_markdown` 的期望（可能嵌套/包装/类型/空串）。
2. **P0 修复后重跑完整 E2E** — 同样本 exercise `diagnosing` + index gate。
3. **未来 observability** — interpreting 失败时归档 Agent OS 原始 response body（仅记录）。
4. **上传** — ~1GB bid 约 5.1s，非瓶颈。

---

## 5. 后续动作

1. 另开 spec/plan 修复 interpretation `report_markdown`（超出本 plan scope）。
2. P0 修复后重跑 `scripts/e2e_diagnosis_flow.py`（样本 `uploads/T-20260716-005`）。
3. 继续用 `artifacts/e2e/<task_id>/` 留证。
