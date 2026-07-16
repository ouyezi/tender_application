# 招标文件解读报告设计规格

**日期：** 2026-07-16  
**状态：** 待用户确认  
**范围：** 在现有标书诊断 Demo 上增加「招标文件解读」阶段与详情页双报告 Tab  
**前置：** [2026-07-16-tender-diagnosis-demo-design.md](./2026-07-16-tender-diagnosis-demo-design.md)

---

## 1. 目标

任务流水线改为**先解读招标文件、再诊断标书**。任务详情页「报告预览」改为 Tab：默认「解读报告」，其次「诊断报告」。解读报告支持下载 HTML。解读内容通过智能体接口生成，本期默认 Mock，接口形状预留真实 HTTP 智能体。

### 成功标准

1. 创建任务后状态为 `interpreting`，解读成功后进入 `diagnosing`，诊断完成后再 `completed`
2. 解读失败 → 任务 `failed`，不启动诊断
3. 详情页报告区有两个 Tab；解读报告可预览 Markdown，并可下载 HTML
4. 诊断报告预览与 DOCX 下载行为与现网一致（仅文案/入口更明确）
5. 存在 `InterpretationAgent` 协议 + `MockInterpretationAgent`；配置可切换至未来 HTTP 实现

### 明确不做

- 本期不接入真实智能体 HTTP
- 解读阶段不支持暂停（仅诊断阶段可 pause）
- 不新增独立子任务表 / 独立路由页
- 不改变诊断引擎协议本身

---

## 2. 方案选择

采用**任务流水线串行**（单一任务状态扩展），不引入独立解读子任务表，不做纯前端占位。

---

## 3. 状态机

```
创建 → interpreting → diagnosing → completed
                ↘ failed（解读失败即停，不进诊断）
任意非终态 → stopped（用户停止）
diagnosing ↔ paused
```

| 状态 | 含义 |
|---|---|
| `interpreting` | 招标文件解读中 |
| `diagnosing` | 解读已完成，正在逐项诊断（**替换原 `running`**） |
| `paused` | 仅诊断阶段可暂停 |
| `completed` / `failed` / `stopped` | 终态，含义同现有 |

**兼容说明：** 原对外状态 `running` 改为 `diagnosing`；列表卡、管理端、测试一并更新。进程重启时，将仍为 `interpreting` / `diagnosing` / `paused` 的任务标为 `stopped`（与现有对 `running`/`paused` 的处理一致）。

**暂停 / 停止：**

- `pause` / `resume`：仅当状态为 `diagnosing` / `paused`
- `stop`：`interpreting`、`diagnosing`、`paused` 均可；解读 Mock 协作中断后标 `stopped`

---

## 4. 数据模型

`diagnosis_tasks` 新增：

| 字段 | 类型 | 说明 |
|---|---|---|
| `interpret_md_path` | 可空字符串 | `data/reports/{task_id}/interpret.md` |
| `interpret_html_path` | 可空字符串 | `data/reports/{task_id}/interpret.html` |

诊断报告字段不变：`report_md_path` / `report_docx_path`。

SQLite demo 可用重建表或轻量迁移；与现有项目迁移策略保持一致。

---

## 5. API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tasks/{id}` | 增加 `interpret_markdown`；`status` 含 `interpreting` / `diagnosing` |
| GET | `/api/tasks/{id}/interpret.html` | 下载解读 HTML；解读成功写出文件后即可下（不必等 `completed`） |
| GET | `/api/tasks/{id}/report.docx` | 不变，仅 `completed` 且文件存在 |

`TaskOut` / `TaskListOut`：`status` 枚举扩展；详情含 `interpret_markdown`（读 MD 文件内联，无则空串）。

---

## 6. 调度顺序

1. 创建任务 → `status=interpreting`，`progress_*` 仍表示诊断项进度（解读阶段可为 `progress_done=0`）
2. 调用 `InterpretationAgent.interpret(...)`（Mock）
3. 成功 → 落盘 `interpret.md` / `interpret.html`，写路径字段 → `status=diagnosing` → 现有诊断循环
4. 诊断完成 → 生成诊断报告 → `completed`
5. 解读抛错 → `failed` + `error_message`，不跑诊断
6. 诊断阶段失败 → `failed`；**已生成的解读报告保留**，仍可预览/下载 HTML

---

## 7. 智能体接口与产物

### 协议

```python
class InterpretationAgent(Protocol):
    async def interpret(
        self, *, task_id: str, tender_path: str, background: str
    ) -> InterpretationResult: ...

@dataclass
class InterpretationResult:
    markdown: str
    title: str = "招标文件解读报告"
```

- 默认：`MockInterpretationAgent`（短延迟 + 固定大纲模板，可嵌入招标文件名）
- 配置预留：`INTERPRETATION_AGENT=mock|http`，`INTERPRETATION_AGENT_URL`（本期只实现 mock）
- HTML 由服务层根据 `markdown` + `title` 生成，不要求 Agent 返回 HTML

### Mock 大纲

1. 项目概况  
2. 招标范围与资质要求  
3. 评分办法要点  
4. 废标/否决条款摘要  
5. 风险提示  

HTML 为自包含文档：Mock 只产出 `markdown`；服务层用简单模板将 markdown 包成完整 HTML 后落盘（避免两套正文漂移）。

### 落盘

`data/reports/{task_id}/interpret.md`、`interpret.html`；诊断报告文件名不变。

---

## 8. 前端

### 任务详情 · 报告预览

- Tab1（默认）：**解读报告** — `MarkdownPreview(interpret_markdown)`
- Tab2：**诊断报告** — 现有 `report_markdown` 逻辑
- 本地 state 切换 Tab，无新路由

### 下载（页头）

- 解读成功后：「下载解读报告」→ `/api/tasks/{id}/interpret.html`
- `completed`：「下载诊断报告」→ `/api/tasks/{id}/report.docx`（原「下载报告」文案收紧）
- Tab 内只预览，不重复放下载

### Tab 空态 / 进行中文案

| 条件 | 解读 Tab | 诊断 Tab |
|---|---|---|
| `interpreting` | 「招标文件解读中…」 | 「解读完成后开始诊断」 |
| `diagnosing` / `paused` | 展示解读预览 | 「诊断进行中…」 |
| `completed` | 解读预览 | 诊断预览 |
| `failed`（无解读稿） | 展示失败原因 | 「未开始诊断」 |
| `failed`（有解读稿，诊断失败） | 解读预览 | 失败提示 / 暂无诊断报告 |
| `stopped` | 有则展示，无则「已停止，暂无报告」 | 同左 |

### 状态徽章

列表、详情、管理端补全：`interpreting`→「解读中」，`diagnosing`→「诊断中」。旧值 `running` 若仍出现在历史数据中，前端映射为「诊断中」；新任务不再写入 `running`。

样式：文字 Tab + 底边高亮，贴合现有 `detail-section`，不引入卡片堆叠。

---

## 9. 测试要点

- 创建后短暂可见 `interpreting`，随后进入 `diagnosing`，最终 `completed`；详情含解读 MD，可下 HTML
- Mock 解读抛错 → `failed`，无诊断结果，无诊断 DOCX
- 解读成功后即可下 HTML；未完成诊断时 DOCX 仍 404
- pause 在 `interpreting` 返回 409；在 `diagnosing` 行为同现有
- 前端状态标签与双 Tab 空态覆盖主要状态

---

## 10. 非目标回顾

真实智能体、解读暂停、独立报告页、子任务表 — 均不在本期范围。
