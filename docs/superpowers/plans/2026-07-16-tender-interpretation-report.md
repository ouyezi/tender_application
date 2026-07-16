# 招标文件解读报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有标书诊断 Demo 上增加「先解读招标文件、再诊断」流水线，详情页双报告 Tab，解读报告可下载 HTML。

**Architecture:** 任务状态改为 `interpreting` → `diagnosing` → `completed`；调度器在诊断循环前调用 `InterpretationAgent`（默认 Mock）；解读 MD/HTML 落盘到 `reports/{task_id}/`；详情页报告区用本地 Tab 切换解读/诊断预览。

**Tech Stack:** 现有 FastAPI + SQLAlchemy + asyncio scheduler；React + Vite；新增 `InterpretationAgent` Protocol。

**Spec:** `docs/superpowers/specs/2026-07-16-tender-interpretation-report-design.md`

---

## File Structure

```text
backend/app/
  config.py                          # + MOCK_INTERPRET_DELAY_SECONDS, INTERPRETATION_AGENT*
  models.py                          # + interpret_md_path, interpret_html_path
  schemas.py                         # + interpret paths on list; interpret_markdown on detail
  db.py                              # recover interpreting/diagnosing
  engine/
    base.py                          # + InterpretationResult, InterpretationAgent
    interpretation_mock.py           # NEW MockInterpretationAgent
  services/
    interpret_report.py              # NEW save md/html from InterpretationResult
    scheduler.py                     # interpret phase then diagnose; status renames
  api/tasks.py                       # create=interpreting; interpret.html; TaskOut fields

backend/tests/
  test_interpretation_agent.py       # NEW
  test_interpret_report.py           # NEW
  test_scheduler.py                  # update statuses + interpret-fail
  test_report.py                     # + HTML download; status diagnosing
  test_tasks.py                      # create status interpreting
  conftest.py                        # monkeypatch interpret delay

frontend/src/
  api.js                             # interpretHtmlUrl
  components/TaskCard.jsx            # status labels
  pages/TaskDetailPage.jsx           # tabs + dual download
  pages/admin/AdminTasksPage.jsx     # diagnosing controls
  App.css                            # tab + status-* styles
```

---

### Task 1: Model & schema fields

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_db.py` (if it asserts column set — extend only if needed)

- [ ] **Step 1: Add config knobs**

In `backend/app/config.py` append:

```python
MOCK_INTERPRET_DELAY_SECONDS = 0.5
INTERPRETATION_AGENT = "mock"  # mock | http (http not implemented this sprint)
INTERPRETATION_AGENT_URL = ""
```

- [ ] **Step 2: Extend `DiagnosisTask` model**

In `backend/app/models.py`, after `report_docx_path` add:

```python
    interpret_md_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    interpret_html_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
```

Change default status comment mentally: callers will set `interpreting` instead of `running`. Keep model default as `"interpreting"`:

```python
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="interpreting")
```

- [ ] **Step 3: Extend schemas**

In `TaskListOut` add:

```python
    interpret_md_path: Optional[str]
    interpret_html_path: Optional[str]
```

In `TaskOut` add:

```python
    interpret_markdown: str = ""
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/config.py backend/app/models.py backend/app/schemas.py
git commit -m "feat: add interpret report fields to task model"
```

---

### Task 2: InterpretationAgent protocol + Mock

**Files:**
- Modify: `backend/app/engine/base.py`
- Create: `backend/app/engine/interpretation_mock.py`
- Create: `backend/tests/test_interpretation_agent.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_interpretation_agent.py`:

```python
import pytest

from app.engine.interpretation_mock import MockInterpretationAgent


@pytest.mark.asyncio
async def test_mock_interpretation_returns_markdown_with_sections(tmp_path):
    agent = MockInterpretationAgent(delay_seconds=0)
    tender = tmp_path / "tender.pdf"
    tender.write_bytes(b"%PDF-1.4")
    result = await agent.interpret(
        task_id="T-20260716-001",
        tender_path=str(tender),
        background="市政工程",
    )
    assert result.title == "招标文件解读报告"
    assert "# 招标文件解读报告" in result.markdown
    for heading in ("项目概况", "招标范围与资质要求", "评分办法要点", "废标/否决条款摘要", "风险提示"):
        assert heading in result.markdown
    assert "tender.pdf" in result.markdown
```

- [ ] **Step 2: Run test — expect FAIL (module missing)**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent.py -v`

Expected: `ModuleNotFoundError` or import error for `interpretation_mock`.

- [ ] **Step 3: Add protocol types to `base.py`**

Append to `backend/app/engine/base.py`:

```python
@dataclass
class InterpretationResult:
    markdown: str
    title: str = "招标文件解读报告"


class InterpretationAgent(Protocol):
    async def interpret(
        self,
        *,
        task_id: str,
        tender_path: str,
        background: str,
    ) -> InterpretationResult: ...
```

- [ ] **Step 4: Implement Mock**

Create `backend/app/engine/interpretation_mock.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from app.engine.base import InterpretationResult


class MockInterpretationAgent:
    def __init__(self, delay_seconds: float = 0.5) -> None:
        self.delay_seconds = delay_seconds

    async def interpret(
        self,
        *,
        task_id: str,
        tender_path: str,
        background: str,
    ) -> InterpretationResult:
        await asyncio.sleep(self.delay_seconds)
        filename = Path(tender_path).name
        bg = background.strip() or "（未提供项目背景）"
        markdown = f"""# 招标文件解读报告

**任务编号：** {task_id}

**招标文件：** {filename}

## 项目概况

基于上传的招标文件「{filename}」与项目背景「{bg}」整理如下要点（Mock）。

## 招标范围与资质要求

- 投标人须具备相应资质与业绩
- 联合体投标要求以招标文件为准

## 评分办法要点

- 技术分与商务分权重以招标文件为准
- 响应性检查为否决项前置条件

## 废标/否决条款摘要

- 未按要求密封、签字盖章
- 实质性偏离招标文件要求

## 风险提示

- 请核对保证金递交方式与截止时间
- 请逐条响应废标条款，避免形式废标
"""
        return InterpretationResult(markdown=markdown.strip() + "\n")
```

- [ ] **Step 5: Run test — expect PASS**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/app/engine/base.py backend/app/engine/interpretation_mock.py backend/tests/test_interpretation_agent.py
git commit -m "feat: add MockInterpretationAgent"
```

---

### Task 3: Interpret report persistence (MD + HTML)

**Files:**
- Create: `backend/app/services/interpret_report.py`
- Create: `backend/tests/test_interpret_report.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_interpret_report.py`:

```python
from pathlib import Path

from app.engine.base import InterpretationResult
from app.services.interpret_report import markdown_to_html_document, save_interpret_reports


def test_markdown_to_html_document_wraps_title_and_body():
    html = markdown_to_html_document("招标文件解读报告", "# 标题\n\n正文段落\n")
    assert "<!DOCTYPE html>" in html
    assert "<title>招标文件解读报告</title>" in html
    assert "正文段落" in html


def test_save_interpret_reports_writes_md_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", tmp_path)
    result = InterpretationResult(markdown="# 招标文件解读报告\n\nhello\n")
    md_path, html_path = save_interpret_reports("T-1", result)
    assert Path(md_path).read_text(encoding="utf-8") == result.markdown
    html = Path(html_path).read_text(encoding="utf-8")
    assert "hello" in html
    assert Path(html_path).name == "interpret.html"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_interpret_report.py -v`

- [ ] **Step 3: Implement service**

Create `backend/app/services/interpret_report.py`:

```python
from __future__ import annotations

import html
import re
from pathlib import Path

from app.config import REPORT_DIR
from app.engine.base import InterpretationResult


def markdown_to_html_document(title: str, markdown: str) -> str:
    body_parts: list[str] = []
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        if line.startswith("# "):
            body_parts.append(f"<h1>{html.escape(line[2:].strip())}</h1>")
        elif line.startswith("## "):
            body_parts.append(f"<h2>{html.escape(line[3:].strip())}</h2>")
        elif line.startswith("### "):
            body_parts.append(f"<h3>{html.escape(line[4:].strip())}</h3>")
        elif line.startswith("- "):
            body_parts.append(f"<li>{html.escape(line[2:].strip())}</li>")
        elif line.strip() == "":
            continue
        else:
            # bold **x**
            escaped = html.escape(line)
            escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
            body_parts.append(f"<p>{escaped}</p>")

    # wrap consecutive <li> in <ul>
    wrapped: list[str] = []
    in_list = False
    for part in body_parts:
        if part.startswith("<li>"):
            if not in_list:
                wrapped.append("<ul>")
                in_list = True
            wrapped.append(part)
        else:
            if in_list:
                wrapped.append("</ul>")
                in_list = False
            wrapped.append(part)
    if in_list:
        wrapped.append("</ul>")

    body = "\n".join(wrapped)
    safe_title = html.escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8"/>\n'
        f"<title>{safe_title}</title>\n"
        "<style>body{font-family:sans-serif;max-width:800px;margin:2rem auto;line-height:1.6;}"
        "h1,h2,h3{margin-top:1.4em;}ul{padding-left:1.2em;}</style>\n"
        "</head>\n"
        f"<body>\n{body}\n</body>\n"
        "</html>\n"
    )


def save_interpret_reports(task_id: str, result: InterpretationResult) -> tuple[str, str]:
    out_dir = REPORT_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "interpret.md"
    html_path = out_dir / "interpret.html"
    md_path.write_text(result.markdown, encoding="utf-8")
    html_path.write_text(
        markdown_to_html_document(result.title, result.markdown),
        encoding="utf-8",
    )
    return str(md_path), str(html_path)
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_interpret_report.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/interpret_report.py backend/tests/test_interpret_report.py
git commit -m "feat: save interpret markdown and HTML reports"
```

---

### Task 4: Scheduler — interpret then diagnose

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/app/db.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Update recovery statuses in `db.py`**

```python
            .where(DiagnosisTask.status.in_(["interpreting", "diagnosing", "running", "paused"]))
```

(Keep `running` for legacy rows.)

- [ ] **Step 2: Update `pause_task` / `stop_task` / `_run`**

In `scheduler.py`:

1. Import:

```python
from app.config import MOCK_ITEM_DELAY_SECONDS, MOCK_INTERPRET_DELAY_SECONDS
from app.engine.interpretation_mock import MockInterpretationAgent
from app.services import interpret_report, report
```

(Remove old `from app.config import MOCK_ITEM_DELAY_SECONDS` if duplicated; keep `report` import.)

2. `pause_task`: only allow `diagnosing` (not `running`/`interpreting`):

```python
        if task.status != "diagnosing":
            raise SchedulerConflict(f"cannot pause task in status {task.status}")
```

3. `resume_task`: from `paused` → `diagnosing` (not `running`):

```python
        task.status = "diagnosing"
```

4. `stop_task`: allow `interpreting`, `diagnosing`, `paused` (and legacy `running`):

```python
        if task.status not in ("interpreting", "diagnosing", "running", "paused"):
            raise SchedulerConflict(...)
```

Same for idle/forced stop branches that set `stopped`.

5. Rewrite `_run` beginning — after loading task, **before** diagnosis loop:

```python
async def _run(task_id: str) -> None:
    ctrl = _get_control(task_id)
    try:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                return
            if task.status in TERMINAL_STATUSES:
                return
            # If resuming mid-diagnosis, skip interpret when paths already set
            need_interpret = not task.interpret_md_path
            snapshot: list[dict[str, Any]] = json.loads(task.config_snapshot or "[]")
            start_idx = task.progress_done
            tender_path = task.tender_path
            bid_path = task.bid_path
            background = task.background or ""
            if need_interpret and task.status not in ("diagnosing", "paused"):
                task.status = "interpreting"
                task.updated_at = utcnow()
                await session.commit()

        if need_interpret:
            await _wait_if_paused(task_id)  # no-op unless somehow paused
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return
            agent = MockInterpretationAgent(delay_seconds=MOCK_INTERPRET_DELAY_SECONDS)
            interpret_result = await agent.interpret(
                task_id=task_id,
                tender_path=tender_path,
                background=background,
            )
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return
            md_path, html_path = interpret_report.save_interpret_reports(task_id, interpret_result)
            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                if task is None or task.status in TERMINAL_STATUSES:
                    return
                task.interpret_md_path = md_path
                task.interpret_html_path = html_path
                task.status = "diagnosing"
                task.updated_at = utcnow()
                await session.commit()

        # existing diagnosis loop — but ensure status stays diagnosing while running items
        engine = MockEngine(delay_seconds=MOCK_ITEM_DELAY_SECONDS)
        documents = {"tender_path": tender_path, "bid_path": bid_path}
        # ... existing for-loop unchanged ...
        # on success set completed as today
```

If interpret raises, the existing `except Exception` marks `failed` — that satisfies spec (no diagnosis). Ensure interpret errors are not swallowed before diagnosis starts.

6. When resuming after pause, `need_interpret` is False because paths exist — diagnosis continues from `progress_done`. Good.

- [ ] **Step 3: Monkeypatch interpret delay in conftest**

In `backend/tests/conftest.py` add:

```python
    monkeypatch.setattr("app.config.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
    monkeypatch.setattr("app.services.scheduler.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
```

- [ ] **Step 4: Update scheduler tests**

In `backend/tests/test_scheduler.py`, replace expectations of `"running"` during active diagnosis with `"diagnosing"` where asserted. Add:

```python
@pytest.mark.asyncio
async def test_interpret_failure_marks_failed_without_diagnosis(client, monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("interpret boom")

    monkeypatch.setattr(
        "app.services.scheduler.MockInterpretationAgent.interpret",
        boom,
    )
    # create task with tiny files like other tests
    ...
    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["results"] == []
    assert detail["interpret_markdown"] == ""
    r = await client.get(f"/api/tasks/{task_id}/report.docx")
    assert r.status_code == 404
```

Also add test that pause during interpreting returns 409:

```python
@pytest.mark.asyncio
async def test_cannot_pause_while_interpreting(client, monkeypatch):
    # make interpret hang until we pause
    gate = asyncio.Event()

    async def slow_interpret(**kwargs):
        await gate.wait()
        from app.engine.base import InterpretationResult
        return InterpretationResult(markdown="# x\n")

    monkeypatch.setattr(
        "app.services.scheduler.MockInterpretationAgent.interpret",
        slow_interpret,
    )
    # create task ...
    # poll until status interpreting
    for _ in range(50):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "interpreting":
            break
        await asyncio.sleep(0.05)
    r = await client.post(f"/api/tasks/{task_id}/pause")
    assert r.status_code == 409
    gate.set()
    await scheduler.wait_for_terminal(task_id, timeout=5)
```

Update existing pause tests: they wait for `running` — change to wait for `diagnosing` (after interpret finishes). With short interpret delay this should still work.

- [ ] **Step 5: Run scheduler + related tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_scheduler.py tests/test_report.py tests/test_tasks.py -v`

Fix any remaining `"running"` assertions to `"diagnosing"` or allow create response `"interpreting"`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scheduler.py backend/app/db.py backend/tests/conftest.py backend/tests/test_scheduler.py
git commit -m "feat: run interpretation before diagnosis in scheduler"
```

---

### Task 5: API — create status, TaskOut fields, HTML download

**Files:**
- Modify: `backend/app/api/tasks.py`
- Modify: `backend/tests/test_tasks.py`
- Modify: `backend/tests/test_report.py`

- [ ] **Step 1: Update `_task_to_out` and helpers**

```python
def _read_interpret_markdown(task: DiagnosisTask) -> str:
    if not task.interpret_md_path:
        return ""
    path = Path(task.interpret_md_path)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _task_to_out(..., interpret_markdown: str = "", ...):
    return TaskOut(
        ...
        interpret_md_path=task.interpret_md_path,
        interpret_html_path=task.interpret_html_path,
        report_md_path=task.report_md_path,
        report_docx_path=task.report_docx_path,
        ...
        report_markdown=report_markdown,
        interpret_markdown=interpret_markdown,
    )
```

Create task:

```python
        status="interpreting",
```

`get_task` / `_load_task_out`:

```python
    return _task_to_out(
        task,
        report_markdown=_read_report_markdown(task),
        interpret_markdown=_read_interpret_markdown(task),
    )
```

Add endpoint **before** `/{task_id}/files/...` if path conflicts matter — place near `report.docx`:

```python
@router.get("/{task_id}/interpret.html")
async def download_interpret_html(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.interpret_html_path:
        raise HTTPException(status_code=404, detail="Interpret report not available")
    path = Path(task.interpret_html_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Interpret report not available")
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        filename=f"{task_id}-interpret.html",
    )
```

- [ ] **Step 2: Tests**

In `test_report.py` add after completed task helper:

```python
@pytest.mark.asyncio
async def test_interpret_html_available_after_interpret(client):
    # create task, wait for terminal completed
    ...
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert "# 招标文件解读报告" in detail["interpret_markdown"]
    r = await client.get(f"/api/tasks/{task_id}/interpret.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "招标文件解读报告" in r.text
```

In `test_tasks.py`, allow create status:

```python
    assert body["status"] in ("interpreting", "diagnosing", "completed", "paused")
```

- [ ] **Step 3: Run tests**

Run: `cd backend && ../.venv/bin/python -m pytest tests/ -v`

- [ ] **Step 4: Commit**

```bash
git add backend/app/api/tasks.py backend/tests/test_tasks.py backend/tests/test_report.py
git commit -m "feat: expose interpret markdown and HTML download API"
```

---

### Task 6: Frontend — status labels, API helper, admin controls

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/components/TaskCard.jsx`
- Modify: `frontend/src/pages/admin/AdminTasksPage.jsx`
- Modify: `frontend/src/App.css` (status colors only in this task)

- [ ] **Step 1: API helper**

```javascript
export function interpretHtmlUrl(id) {
  return `/api/tasks/${id}/interpret.html`
}
```

- [ ] **Step 2: Shared status labels pattern**

In `TaskCard.jsx`, `TaskDetailPage.jsx` (next task), `AdminTasksPage.jsx`:

```javascript
const STATUS_LABELS = {
  interpreting: '解读中',
  diagnosing: '诊断中',
  running: '诊断中', // legacy
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}
```

Default fallback status: `interpreting` or keep showing raw.

- [ ] **Step 3: Admin controls**

Replace `status === 'running'` pause/stop block with:

```javascript
{(status === 'diagnosing' || status === 'running') && (
  // pause + stop
)}
{status === 'interpreting' && (
  <button ... stop only>...</button>
)}
```

- [ ] **Step 4: CSS status badges**

```css
.status-interpreting {
  /* same family as running — e.g. blue/info */
}
.status-diagnosing {
  /* reuse running colors */
}
```

Copy `.status-running` rules to `.status-diagnosing` and add a distinct `.status-interpreting` (slightly different hue is fine).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/components/TaskCard.jsx frontend/src/pages/admin/AdminTasksPage.jsx frontend/src/App.css
git commit -m "feat: frontend status labels for interpreting/diagnosing"
```

---

### Task 7: Frontend — TaskDetailPage report tabs + downloads

**Files:**
- Modify: `frontend/src/pages/TaskDetailPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: Update imports and poll set**

```javascript
import { fileUrl, getTask, interpretHtmlUrl, reportDocxUrl } from '../api'

const POLL_STATUSES = new Set(['interpreting', 'diagnosing', 'running', 'paused'])
```

Add state:

```javascript
const [reportTab, setReportTab] = useState('interpret') // 'interpret' | 'diagnosis'
```

- [ ] **Step 2: Header downloads**

```jsx
<div className="page-header-actions">
  {task.interpret_html_path && (
    <a className="btn btn-secondary" href={interpretHtmlUrl(task.id)}>
      下载解读报告
    </a>
  )}
  {status === 'completed' && (
    <a className="btn btn-primary" href={reportDocxUrl(task.id)}>
      下载诊断报告
    </a>
  )}
</div>
```

Prefer `task.interpret_html_path` OR `Boolean(task.interpret_markdown)` for showing the button (API always returns path fields on list/detail).

- [ ] **Step 3: Report section with tabs**

Replace single「报告预览」section with:

```jsx
<section className="detail-section">
  <h2>报告预览</h2>
  <div className="report-tabs" role="tablist">
    <button
      type="button"
      role="tab"
      aria-selected={reportTab === 'interpret'}
      className={reportTab === 'interpret' ? 'report-tab active' : 'report-tab'}
      onClick={() => setReportTab('interpret')}
    >
      解读报告
    </button>
    <button
      type="button"
      role="tab"
      aria-selected={reportTab === 'diagnosis'}
      className={reportTab === 'diagnosis' ? 'report-tab active' : 'report-tab'}
      onClick={() => setReportTab('diagnosis')}
    >
      诊断报告
    </button>
  </div>

  {reportTab === 'interpret' ? (
    status === 'interpreting' && !task.interpret_markdown ? (
      <p className="report-pending">招标文件解读中…</p>
    ) : task.interpret_markdown ? (
      <MarkdownPreview markdown={task.interpret_markdown} />
    ) : status === 'failed' ? (
      <p className="page-error">{task.error_message || '解读失败'}</p>
    ) : status === 'stopped' ? (
      <p className="empty-state-hint">已停止，暂无报告</p>
    ) : (
      <p className="empty-state-hint">暂无解读报告</p>
    )
  ) : status === 'interpreting' ? (
    <p className="report-pending">解读完成后开始诊断</p>
  ) : (status === 'diagnosing' || status === 'running' || status === 'paused') &&
    !task.report_markdown ? (
    <p className="report-pending">诊断进行中…</p>
  ) : task.report_markdown ? (
    <MarkdownPreview markdown={task.report_markdown} />
  ) : status === 'failed' && !task.interpret_markdown ? (
    <p className="empty-state-hint">未开始诊断</p>
  ) : status === 'failed' ? (
    <p className="page-error">{task.error_message || '诊断失败'}</p>
  ) : (
    <p className="empty-state-hint">暂无报告</p>
  )}
</section>
```

- [ ] **Step 4: Tab CSS**

```css
.report-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border, #ddd);
  margin-bottom: 1rem;
}
.report-tab {
  background: none;
  border: none;
  padding: 0.5rem 1rem;
  cursor: pointer;
  color: inherit;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.report-tab.active {
  border-bottom-color: var(--accent, #2563eb);
  font-weight: 600;
}
.page-header-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
  align-items: center;
}
```

- [ ] **Step 5: Manual smoke (optional)**

Start stack, create task, confirm: badge 解读中 → 诊断中 → 已完成；默认 Tab 为解读；可下 HTML；完成后可下诊断 DOCX。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/TaskDetailPage.jsx frontend/src/App.css
git commit -m "feat: dual report tabs and interpret HTML download on task detail"
```

---

### Task 8: Final verification

- [ ] **Step 1: Backend full suite**

Run: `cd backend && ../.venv/bin/python -m pytest tests/ -v`

Expected: all PASS.

- [ ] **Step 2: Spec checklist**

Confirm against spec:

| Requirement | Task |
|---|---|
| interpreting → diagnosing → completed | 4 |
| interpret fail → failed, no diagnosis | 4 |
| Dual tabs + MD preview | 7 |
| HTML download | 5 + 7 |
| Mock agent + config knobs | 2 + 1 |
| pause only diagnosing | 4 |
| recover interpreting/diagnosing | 4 |

- [ ] **Step 3: Commit any leftover fixes** (if needed)

---

## Self-Review (plan vs spec)

1. **Spec coverage:** Status machine, fields, APIs, scheduler order, Mock agent, HTML from service layer, frontend tabs/downloads/admin — all mapped to Tasks 1–7.
2. **Placeholders:** None intentionally left; HTML converter fully specified.
3. **Type consistency:** `InterpretationResult(markdown, title)` used in agent, save service, and scheduler; no Agent-returned `html` field.
