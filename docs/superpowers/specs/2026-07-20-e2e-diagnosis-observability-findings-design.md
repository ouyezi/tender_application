# 完整标书诊断 E2E 取证与问题清单设计

## 目标

1. 用本机样例 `uploads/T-20260716-005/{tender,bid}.docx` 跑通一次完整 API 诊断流程。
2. 跟进中间状态、阶段耗时与关键产物，形成可复现证据包。
3. 输出问题与优化清单（`findings.md`）；**本期不改业务代码**，只增强 E2E/取证能力。

## 已确认决策

| 决策点 | 选择 |
|---|---|
| 核心目标 | 跑通 + 取证 + findings；业务修复另做 |
| 证据粒度 | 终态摘要 + 阶段产物快照 + 阶段耗时分解 |
| 改动范围 | 仅 E2E 脚本与观测/取证（范围 A） |
| 执行方式 | 最小脚本增强与跑流程并行（方案 C） |
| 架构方案 | 纯脚本增强型 E2E（方案 1） |

## 背景与现状

真实分类批诊断、索引门闩、`tender_batch_diagnosis_app` 与
`scripts/e2e_diagnosis_flow.py` 已落地（见
`2026-07-19-real-batch-diagnosis-and-e2e-design.md`）。

计划中 Task 6 Step 4「真实文件完整 E2E」仍待验收。现有脚本仅轮询
`status` / `progress` / `failure_stage`，缺少：

- status 变迁时间线与阶段耗时
- checklist / results / report / index-status 落盘
- 结构化 findings 产出

大标书（bid ≈ 1GB）耗时长，不落盘极易丢失排查证据。

## 方案选择

采用**纯脚本增强型 E2E**：

- 只改 `scripts/e2e_diagnosis_flow.py`（同文件内可抽小函数）。
- 通过已有 API 取证，不读服务端本地 log 文件，避免路径耦合。
- 业务侧问题（性能、提示词、Agent 契约、检索质量等）写入 findings，本期不实现。

未采用：旁路独立监控脚本（两套入口）；服务端埋点（超出范围 A）。

## 运行流程

1. 确认 `startup.py` 已在 `--base-url`（默认 `http://127.0.0.1:8888`）运行；
   `config.local.json` 与 `tender_batch_diagnosis_app` 可用。
2. 合入脚本最小增强后执行：

```bash
.venv/bin/python scripts/e2e_diagnosis_flow.py \
  --tender uploads/T-20260716-005/tender.docx \
  --bid uploads/T-20260716-005/bid.docx
```

3. 跑的过程中按需补采（例如索引长时间未 ready 时加密 `index-status` 采样）。
4. 终态后根据证据包补全 `findings.md`。

## 产物布局

目录根由 `--artifacts-dir` 控制，默认 `artifacts/e2e`。每次 run：

```text
artifacts/e2e/<task_id>/
  meta.json              # 命令参数、base-url、起止时间、退出码
  timeline.json          # status/progress 采样 + 阶段耗时
  task_final.json        # GET /api/tasks/{id} 终态
  checklist.json         # GET /api/tasks/{id}/checklist（若可得）
  results_summary.json   # compliance 分布、offline/file 计数、mock 检测
  index_status.json      # knowledge index-status 末次或关键变迁
  report.docx            # 成功时下载
  interpret.md           # 若 detail 含 interpret_markdown 则落盘
  findings.md            # 问题与优化清单
```

`artifacts/` 加入 `.gitignore`，大产物不入库。设计与计划文档可入库；
`findings.md` 可择要摘录进 `docs/superpowers/` 的分析笔记（可选，非必须）。

## 阶段耗时

- 以 task `status` **首次变为某值**的时间戳为阶段边界
  （如 `interpreting` → `generating_checklist` → `diagnosing` → `completed`）。
- 另计 `upload_seconds`（`POST /api/tasks` 返回前耗时）。
- 索引等待无法从 status 单独拆出时，用
  `GET /api/workspaces/{task_id}/knowledge/index-status` 采样辅助标注
  `bid_index_ready_at`。
- `timeline.json` 同时保留原始采样点，便于复算。

## 脚本行为

| 能力 | 行为 |
|---|---|
| `--artifacts-dir` | 默认 `artifacts/e2e`；写入 `<dir>/<task_id>/` |
| 轮询采样 | 每次 poll 记 `t, status, progress_done/total, failure_stage`；status 变化时醒目打印 |
| 旁路采样 | 默认每 6 次 poll（约 30s，随 `--poll-seconds` 变），以及 status 进入/离开 `diagnosing` 时，各拉一次 index-status；可用 `--index-sample-every` 覆盖 |
| 终态落盘 | 成功或失败都写 `meta` / `timeline` / `task_final`；成功再写 checklist、report、results_summary |
| findings 骨架 | 自动生成含摘要与耗时表的骨架；分析段落可后补 |

CLI 兼容现有参数：`--base-url`、`--tender`、`--bid`、`--timeout-seconds`、`--poll-seconds`。

## 成功 / 失败判定

**成功（退出 0）**须同时满足：

1. 终态 `completed`
2. checklist 至少 1 条 item
3. results 至少 1 条
4. 存在 file 项时，evidence 不含 `mock evidence for checklist item`
5. `report.docx` HTTP 200 且落盘非空

**失败（非 0）**：

- 打印 `status` / `failure_stage` / `error_message`
- 仍尽量落盘已采集证据（含部分 checklist，若 API 已有）
- `findings.md` 自动填入失败阶段与时间线摘要

**不做**：脚本内改任务状态、业务重试、读取服务端本地日志文件。

## findings.md 模板

1. **Run 摘要**：task_id、样例路径、起止时间、终态、退出码
2. **阶段耗时表**：upload / 各 status 区间 / bid_index_ready（若有）/ 总耗时
3. **产出抽检**：checklist 数量、file vs offline、compliance 分布、mock 检测、报告可下载性
4. **问题清单**：现象、证据路径、影响、优先级（P0 阻塞 / P1 质量 / P2 优化）
5. **优化建议**：性能、提示词、Agent 契约、检索等——**只记录，本期不实现**
6. **后续动作**：建议的下一轮修复/计划入口

## 验收顺序

1. 脚本增强可 dry-run（`--help`；缺文件退出 2）
2. 启动服务 → 跑完整 E2E（接受数小时级耗时）
3. 检查 `artifacts/e2e/<task_id>/` 关键文件齐全，`timeline.json` 含阶段边界
4. 补全 `findings.md`（至少覆盖阻塞项与 Top 优化点）
5. 设计/计划入库；大产物保持 gitignore

## 明确不做

- 改 scheduler、批诊断引擎、Agent 契约、提示词或检索实现
- UI 浏览器自动化
- 把 1GB 样例或 E2E 大产物提交进 git
- 生产 Mock 回退或新的引擎配置开关

## 与既有文档关系

- 承接并完成
  `2026-07-19-real-batch-diagnosis-and-e2e-design.md` /
  计划 Task 6 Step 4 的「真实跑通」验收缺口。
- 补上「可观测取证 + 问题清单」；不重复实现批诊断引擎本身。
- findings 中列出的业务优化，应另开 spec/plan 再实施。
