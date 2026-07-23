# 解读报告 HTML 按需生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 强化页头「下载解读报告」——解读阶段只存 Markdown；用户按需触发智能体生成 JSON，后端固定模板渲染为参考样式的单文件 HTML，支持直接下载与确认后重新生成。

**Architecture:** 新智能体 `tender_interpret_html_report_app` 输入解读 Markdown、输出 JSON；`interpret_html_report.py` 固定 CSS/JS + 渲染；`interpret_html_service.py` 独立 asyncio 后台任务 + 内存 lane 状态；`readiness` 暴露 `interpret_html_*` 三字段；前端页头按钮状态机 + 2s 轮询。

**Tech Stack:** Python 3 / FastAPI / Pydantic / pytest；React (TaskDetailPage.jsx)；Agent OS

**Spec:** `docs/superpowers/specs/2026-07-23-interpret-html-report-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/schemas/interpret_html_report.py` | **新建** Pydantic JSON 模型 |
| `backend/app/templates/interpret_html_report.py` | **新建** CSS/JS 常量 + `render_interpret_html_report()` |
| `backend/app/engine/interpret_html_agent_os.py` | **新建** Agent OS 调用 + JSON 解析 |
| `backend/app/services/interpret_html_service.py` | **新建** 异步生成编排、lane 状态、落盘 |
| `backend/app/services/interpret_report.py` | 解读阶段仅写 Markdown |
| `backend/app/services/scheduler.py` | 解读完成不再写 `interpret_html_path` |
| `backend/app/services/task_readiness.py` | 扩展 `interpret_html_*` |
| `backend/app/schemas.py` | `TaskReadinessOut` 新字段 |
| `backend/app/api/tasks.py` | POST `generate-interpret-html` |
| `backend/app/config.py` | app 名与 timeout |
| `backend/app/services/agent_os.py` | `AgentOSSettings` 新 timeout 字段（若尚未有） |
| `frontend/src/api.js` | `generateInterpretHtml()` |
| `frontend/src/pages/TaskDetailPage.jsx` | 页头按钮状态机 + 轮询 |
| `docs/agents_config/tender_interpret_html_report.json` | **新建** 智能体配置草案 |
| `backend/tests/test_interpret_html_*.py` | **新建** 单元/集成测试 |
| `backend/tests/test_interpret_report.py` | 更新：不再期望 auto HTML |
| `backend/tests/test_scheduler.py` | 更新：解读后无 html path |
| `backend/tests/test_report.py` | 更新/新增按需生成流程 |

---

### Task 1: JSON Pydantic 模型

**Files:**
- Create: `backend/app/schemas/interpret_html_report.py`
- Test: `backend/tests/test_interpret_html_schema.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_interpret_html_schema.py`:

```python
import pytest
from pydantic import ValidationError

from app.schemas.interpret_html_report import InterpretHtmlReportData


MINIMAL_PAYLOAD = {
    "schema_version": "1",
    "meta": {
        "title": "项目 — 招标分析报告",
        "subtitle": "招标编号：X | 截标：2026-07-27",
        "project_key": "tender_test",
    },
    "overview": {"rows": [{"label": "项目名称", "value": "测试", "label2": "编号", "value2": "N-1"}]},
    "risks": [{"level": "high", "title": "风险", "desc": "说明"}],
    "tasks": {"p0": [], "p1": [], "p2": []},
    "checklist": [{"section": "资质", "items": ["营业执照"], "redline": False}],
    "key_info": {
        "timeline": [],
        "qualification": [],
        "commercial": [],
        "technical": [],
    },
    "strategy": {"advantage": "优势", "risk_avoid": "规避", "price": "报价"},
    "scoring": [],
}


def test_parses_minimal_payload():
    data = InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)
    assert data.meta.title.startswith("项目")
    assert data.risks[0].level == "high"


def test_rejects_invalid_risk_level():
    bad = {**MINIMAL_PAYLOAD, "risks": [{"level": "critical", "title": "x", "desc": "y"}]}
    with pytest.raises(ValidationError):
        InterpretHtmlReportData.model_validate(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_schema.py -v`

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `backend/app/schemas/interpret_html_report.py`:

```python
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MetaBlock(BaseModel):
    title: str = Field(..., min_length=1)
    subtitle: str = ""
    project_key: str = ""


class OverviewRow(BaseModel):
    label: str
    value: str
    label2: Optional[str] = None
    value2: Optional[str] = None
    colspan: Optional[int] = None


class OverviewBlock(BaseModel):
    rows: list[OverviewRow] = Field(default_factory=list)


class RiskItem(BaseModel):
    level: Literal["high", "mid", "low"]
    title: str
    desc: str


class TaskItem(BaseModel):
    name: str
    owner: str = ""
    deadline: str = ""


class TasksBlock(BaseModel):
    p0: list[TaskItem] = Field(default_factory=list)
    p1: list[TaskItem] = Field(default_factory=list)
    p2: list[TaskItem] = Field(default_factory=list)


class ChecklistSection(BaseModel):
    section: str
    items: list[str] = Field(default_factory=list)
    redline: bool = False


class TimelineRow(BaseModel):
    label: str
    value: str
    note: str = ""


class KeyValueRow(BaseModel):
    label: str
    value: str


class KeyInfoBlock(BaseModel):
    timeline: list[TimelineRow] = Field(default_factory=list)
    qualification: list[KeyValueRow] = Field(default_factory=list)
    commercial: list[KeyValueRow] = Field(default_factory=list)
    technical: list[KeyValueRow] = Field(default_factory=list)


class StrategyBlock(BaseModel):
    advantage: str = ""
    risk_avoid: str = ""
    price: str = ""


class ScoringRow(BaseModel):
    dimension: str
    score: str
    weight: str = ""
    criteria: str = ""
    strategy: str = ""


class InterpretHtmlReportData(BaseModel):
    schema_version: Literal["1"]
    meta: MetaBlock
    overview: OverviewBlock
    risks: list[RiskItem] = Field(default_factory=list)
    tasks: TasksBlock = Field(default_factory=TasksBlock)
    checklist: list[ChecklistSection] = Field(default_factory=list)
    key_info: KeyInfoBlock = Field(default_factory=KeyInfoBlock)
    strategy: StrategyBlock = Field(default_factory=StrategyBlock)
    scoring: list[ScoringRow] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_schema.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/interpret_html_report.py backend/tests/test_interpret_html_schema.py
git commit -m "feat: add interpret HTML report JSON schema models"
```

---

### Task 2: HTML 模板渲染器

**Files:**
- Create: `backend/app/templates/interpret_html_report.py`
- Test: `backend/tests/test_interpret_html_render.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_interpret_html_render.py`:

```python
from app.schemas.interpret_html_report import InterpretHtmlReportData
from app.templates.interpret_html_report import render_interpret_html_report
from tests.test_interpret_html_schema import MINIMAL_PAYLOAD


def test_render_includes_doctype_title_and_risk_section():
    data = InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)
    html = render_interpret_html_report(data, task_id="T-1")
    assert html.startswith("<!DOCTYPE html>")
    assert "项目 — 招标分析报告" in html
    assert "风险雷达" in html
    assert "风险" in html
    assert "toggleCard" in html
    assert "progressFill" in html


def test_render_escapes_html_injection():
    payload = {
        **MINIMAL_PAYLOAD,
        "risks": [{"level": "low", "title": "<script>alert(1)</script>", "desc": "ok"}],
    }
    data = InterpretHtmlReportData.model_validate(payload)
    html = render_interpret_html_report(data, task_id="T-1")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_redline_checklist_class():
    payload = {
        **MINIMAL_PAYLOAD,
        "checklist": [{"section": "红线", "items": ["废标项"], "redline": True}],
    }
    data = InterpretHtmlReportData.model_validate(payload)
    html = render_interpret_html_report(data, task_id="T-1")
    assert "check-item redline" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_render.py -v`

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `backend/app/templates/interpret_html_report.py`:

1. 从参考样例 `招标分析报告-2026-2027年度慰问品采购项目.html` 复制 `<style>...</style>` 为模块常量 `INTERPRET_HTML_CSS`（第 7–106 行）。
2. 复制 `<script>...</script>` 为 `INTERPRET_HTML_JS`（第 515–543 行），保留 `PROJECT_KEY` 占位，渲染时替换为 `meta.project_key or f"tender_{task_id}"`。
3. 实现辅助函数：
   - `_escape(text: str) -> str` → `html.escape`
   - `_rich_text(text: str) -> str` → escape 后仅将 `\n` 转 `<br>`（strategy 字段用）
   - `_render_overview_rows`, `_render_risks`, `_render_tasks`, `_render_checklist`, `_render_key_info`, `_render_strategy`, `_render_scoring`
4. 公开函数：

```python
def render_interpret_html_report(data: InterpretHtmlReportData, *, task_id: str) -> str:
    project_key = data.meta.project_key or f"tender_{task_id}"
    js = INTERPRET_HTML_JS.replace("const PROJECT_KEY = 'tender_DF20260720DF04';", f"const PROJECT_KEY = '{project_key}';")
    body = ...  # 8 cards: 项目速览/风险雷达/投标任务清单/投标检查清单/关键信息摘录/投标策略建议/评分要点分析/团队备注
    return (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
        f"<meta charset=\"UTF-8\">\n<title>{_escape(data.meta.title)}</title>\n"
        f"<style>\n{INTERPRET_HTML_CSS}\n</style>\n</head>\n<body>\n"
        f"{body}\n<script>\n{js}\n</script>\n</body>\n</html>"
    )
```

卡片标题与图标固定为参考 HTML（📋 ⚠️ 📅 ✅ 🔑 🎯 📊 📝）；第 8 节 body 为固定空 `<textarea>`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_render.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/interpret_html_report.py backend/tests/test_interpret_html_render.py
git commit -m "feat: add interpret HTML report template renderer"
```

---

### Task 3: 解读阶段不再自动生成 HTML

**Files:**
- Modify: `backend/app/services/interpret_report.py`
- Modify: `backend/app/services/scheduler.py` (~664-675)
- Modify: `backend/tests/test_interpret_report.py`
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/tests/test_report.py`

- [ ] **Step 1: Update failing test**

In `backend/tests/test_interpret_report.py`, replace `test_save_interpret_reports_writes_md_and_html`:

```python
def test_save_interpret_reports_writes_md_only(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "reports").mkdir()
    result = InterpretationResult(markdown="# 招标文件解读报告\n\nhello\n")
    md_path = save_interpret_reports("T-1", result)
    assert Path(md_path).read_text(encoding="utf-8") == result.markdown
    assert not (tmp_path / "reports" / "T-1" / "interpret.html").exists()
    artifact_report = tmp_path / "uploads" / "T-1" / "report"
    assert (artifact_report / "interpret.md").is_file()
    assert not (artifact_report / "interpret.html").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_report.py::test_save_interpret_reports_writes_md_only -v`

Expected: FAIL (still writes html)

- [ ] **Step 3: Implement**

`backend/app/services/interpret_report.py`:

```python
def save_interpret_reports(task_id: str, result: InterpretationResult) -> str:
    out_dir = REPORT_DIR / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "interpret.md"
    md_path.write_text(result.markdown, encoding="utf-8")
    artifact.sync_to_artifact_report(task_id, md_path)
    return str(md_path)
```

Update `artifact.sync_to_artifact_report` signature if needed — 仅同步 md（检查 `backend/app/services/artifact.py`，若函数要求 html 参数则改为可选或 overload）。

`backend/app/services/scheduler.py`:

```python
md_path = interpret_report.save_interpret_reports(task_id, interpret_result)
...
task.interpret_md_path = md_path
# 删除 task.interpret_html_path = html_path
```

- [ ] **Step 4: Fix scheduler/report tests**

In `backend/tests/test_scheduler.py`, remove or invert assertions expecting `interpret_html_path` immediately after interpret.

In `backend/tests/test_report.py`, remove `test_interpret_html_available_after_interpret` auto-download expectation; defer HTML test to Task 6.

- [ ] **Step 5: Run tests**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_report.py tests/test_scheduler.py tests/test_report.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/interpret_report.py backend/app/services/scheduler.py backend/app/services/artifact.py backend/tests/test_interpret_report.py backend/tests/test_scheduler.py backend/tests/test_report.py
git commit -m "refactor: save interpret markdown only during interpretation stage"
```

---

### Task 4: Agent OS 包装器

**Files:**
- Create: `backend/app/engine/interpret_html_agent_os.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/agent_os.py` (settings timeout)
- Test: `backend/tests/test_interpret_html_agent_os.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_interpret_html_agent_os.py`:

```python
import json

import pytest

from app.engine.interpret_html_agent_os import (
    TENDER_INTERPRET_HTML_REPORT_APP_NAME,
    AgentOSInterpretHtmlAgent,
    InterpretHtmlAgentResponseError,
)
from app.schemas.interpret_html_report import InterpretHtmlReportData
from tests.test_interpret_html_schema import MINIMAL_PAYLOAD


class FakeClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    async def invoke_app(self, app_name, input_data, log_context=None):
        self.calls.append((app_name, input_data))
        return self.response


@pytest.mark.asyncio
async def test_invoke_passes_interpret_report():
    client = FakeClient({"output": json.dumps(MINIMAL_PAYLOAD)})
    agent = AgentOSInterpretHtmlAgent(client=client)
    result = await agent.generate(task_id="T-1", interpret_report="# 解读\n")
    assert isinstance(result, InterpretHtmlReportData)
    assert client.calls[0][0] == TENDER_INTERPRET_HTML_REPORT_APP_NAME
    assert client.calls[0][1]["interpret_report"] == "# 解读\n"


@pytest.mark.asyncio
async def test_raises_on_invalid_json():
    client = FakeClient({"output": "not json"})
    agent = AgentOSInterpretHtmlAgent(client=client)
    with pytest.raises(InterpretHtmlAgentResponseError):
        await agent.generate(task_id="T-1", interpret_report="# x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_agent_os.py -v`

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `backend/app/engine/interpret_html_agent_os.py`（模式参照 `interpretation_agent_os.py`）：

```python
TENDER_INTERPRET_HTML_REPORT_APP_NAME = "tender_interpret_html_report_app"

class InterpretHtmlAgentResponseError(RuntimeError):
    pass

def _extract_json(response: dict) -> dict:
    for key in ("report_json", "output", "result"):
        raw = response.get(key)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            return json.loads(raw)
    raise InterpretHtmlAgentResponseError("missing JSON in agent response")

class AgentOSInterpretHtmlAgent:
    def __init__(self, *, client=None, app_name=TENDER_INTERPRET_HTML_REPORT_APP_NAME):
        self.app_name = app_name
        self._client = client

    async def generate(self, *, task_id: str, interpret_report: str) -> InterpretHtmlReportData:
        client = self._client or AgentOSClient()
        payload = await client.invoke_app(
            self.app_name,
            {"interpret_report": interpret_report},
            log_context={"task_id": task_id},
        )
        data = _extract_json(payload)
        return InterpretHtmlReportData.model_validate(data)
```

Add to `backend/app/config.py`:

```python
INTERPRET_HTML_APP_NAME = "tender_interpret_html_report_app"
INTERPRET_HTML_INVOKE_TIMEOUT_SECONDS = 600.0
```

Wire timeout in `AgentOSSettings` + `invoke_app` path（与 checklist timeout 同样方式）。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_agent_os.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/interpret_html_agent_os.py backend/app/config.py backend/app/services/agent_os.py backend/tests/test_interpret_html_agent_os.py
git commit -m "feat: add Agent OS wrapper for interpret HTML report generation"
```

---

### Task 5: interpret_html_service + readiness

**Files:**
- Create: `backend/app/services/interpret_html_service.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services/task_readiness.py`
- Test: `backend/tests/test_interpret_html_service.py`
- Test: `backend/tests/test_task_readiness.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_interpret_html_service.py`:

```python
import asyncio
from pathlib import Path

import pytest

from app.engine.base import InterpretationResult
from app.services import interpret_html_service, interpret_report


@pytest.fixture
def report_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("app.services.interpret_html_service.REPORT_DIR", tmp_path / "reports")
    return tmp_path / "reports"


@pytest.mark.asyncio
async def test_start_generation_writes_html(report_dir, monkeypatch):
    interpret_report.save_interpret_reports(
        "T-HTML",
        InterpretationResult(markdown="# 解读报告\n\n内容"),
    )

    async def fake_generate(task_id, interpret_report_text):
        from app.schemas.interpret_html_report import InterpretHtmlReportData
        from tests.test_interpret_html_schema import MINIMAL_PAYLOAD
        return InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)

    monkeypatch.setattr(interpret_html_service, "_generate_data", fake_generate)

    await interpret_html_service.start("T-HTML")
    await asyncio.sleep(0.05)
    while interpret_html_service.is_lane_active("T-HTML"):
        await asyncio.sleep(0.01)

    html_path = report_dir / "T-HTML" / "interpret.html"
    assert html_path.is_file()
    assert "招标分析报告" in html_path.read_text(encoding="utf-8")
    assert interpret_html_service.get_error("T-HTML") is None


@pytest.mark.asyncio
async def test_start_raises_conflict_when_lane_active(report_dir, monkeypatch):
    interpret_html_service._set_lane_active_for_test("T-HTML", True)
    with pytest.raises(interpret_html_service.InterpretHtmlConflict):
        await interpret_html_service.start("T-HTML")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_service.py -v`

Expected: FAIL

- [ ] **Step 3: Implement service**

Create `backend/app/services/interpret_html_service.py`:

```python
_active: set[str] = set()
_errors: dict[str, str] = {}

class InterpretHtmlConflict(Exception):
    pass

def is_lane_active(task_id: str) -> bool:
    return task_id in _active

def get_error(task_id: str) -> str | None:
    return _errors.get(task_id)

async def start(task_id: str) -> None:
    if task_id in _active:
        raise InterpretHtmlConflict("interpret_html_lane_active")
    _active.add(task_id)
    _errors.pop(task_id, None)
    asyncio.create_task(_run(task_id))

async def _run(task_id: str) -> None:
    try:
        md = _read_interpret_md(task_id)
        data = await _generate_data(task_id, md)
        html = render_interpret_html_report(data, task_id=task_id)
        path = REPORT_DIR / task_id / "interpret.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        await _persist_html_path(task_id, str(path))
        artifact.sync...
    except Exception as exc:
        _errors[task_id] = str(exc)[:240]
    finally:
        _active.discard(task_id)
```

Extend `TaskReadinessOut` in `backend/app/schemas.py`:

```python
interpret_html_ready: bool = False
interpret_html_lane_active: bool = False
interpret_html_error: Optional[str] = None
```

Update `compute_task_readiness()`:

```python
from app.services import interpret_html_service

interpret_html_ready = bool(task.interpret_html_path and Path(task.interpret_html_path).is_file())
return {
    ...
    "interpret_html_ready": interpret_html_ready,
    "interpret_html_lane_active": interpret_html_service.is_lane_active(task_id),
    "interpret_html_error": interpret_html_service.get_error(task_id),
}
```

- [ ] **Step 4: Run tests**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_service.py tests/test_task_readiness.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/interpret_html_service.py backend/app/schemas.py backend/app/services/task_readiness.py backend/tests/test_interpret_html_service.py backend/tests/test_task_readiness.py
git commit -m "feat: add interpret HTML async generation service and readiness fields"
```

---

### Task 6: API 端点 + 集成测试

**Files:**
- Modify: `backend/app/api/tasks.py`
- Test: `backend/tests/test_interpret_html_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_interpret_html_api.py`:

```python
import asyncio

import pytest


@pytest.mark.asyncio
async def test_generate_interpret_html_flow(client, monkeypatch):
    # 使用 conftest 创建并完成解读的任务
    ...  # 参照 test_report.py _create_task + wait_for_terminal

    task_id = ...
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert not detail.get("interpret_html_path")

    r = await client.post(f"/api/tasks/{task_id}/actions/generate-interpret-html")
    assert r.status_code == 202

    async def fake_generate(task_id, interpret_report_text):
        from tests.test_interpret_html_schema import MINIMAL_PAYLOAD
        from app.schemas.interpret_html_report import InterpretHtmlReportData
        return InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)

    monkeypatch.setattr("app.services.interpret_html_service._generate_data", fake_generate)

    for _ in range(50):
        readiness = (await client.get(f"/api/tasks/{task_id}/readiness")).json()
        if readiness["interpret_html_ready"]:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("interpret html not ready")

    r2 = await client.get(f"/api/tasks/{task_id}/interpret.html")
    assert r2.status_code == 200
    assert "text/html" in r2.headers.get("content-type", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_api.py -v`

Expected: FAIL (404 on POST)

- [ ] **Step 3: Add endpoint**

In `backend/app/api/tasks.py`:

```python
from app.services import interpret_html_service

@router.post("/{task_id}/actions/generate-interpret-html", status_code=status.HTTP_202_ACCEPTED)
async def action_generate_interpret_html(task_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.interpret_md_path or not Path(task.interpret_md_path).is_file():
        raise HTTPException(status_code=404, detail="Interpret report not available")
    try:
        await interpret_html_service.start(task_id)
    except interpret_html_service.InterpretHtmlConflict:
        raise HTTPException(status_code=409, detail="interpret_html_lane_active")
    return {"task_id": task_id, "status": "generating_interpret_html"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_interpret_html_api.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/tasks.py backend/tests/test_interpret_html_api.py
git commit -m "feat: add generate-interpret-html API action"
```

---

### Task 7: 智能体配置草案

**Files:**
- Create: `docs/agents_config/tender_interpret_html_report.json`

- [ ] **Step 1: Create agent config JSON**

参照 `docs/agents_config/tender_checklist_generator.json` 结构，创建配置草案：

- `agent.enName`: `tender_interpret_html_report`
- `application.enName`: `tender_interpret_html_report_app`
- `invoke.requiredInputs`: `["interpret_report"]`
- `io.formatOutput`: `true`
- `outputSchema`: 与 §5 JSON schema 字段对齐（或 output 为 JSON string）
- `systemPrompt`: 说明从解读 Markdown 提取 8 区块内容，只输出 JSON，`schema_version` 必须为 `"1"`，禁止 HTML/CSS

- [ ] **Step 2: Commit**

```bash
git add docs/agents_config/tender_interpret_html_report.json
git commit -m "docs: add tender interpret HTML report agent config draft"
```

- [ ] **Step 3: 发布智能体（人工/单独会话）**

使用 `agent-create-publish` skill 将草案发布到 Agent OS。**实现计划内可用 fake `_generate_data` 跑通测试；联调前需完成发布。**

---

### Task 8: 前端页头按钮状态机

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/pages/TaskDetailPage.jsx`

- [ ] **Step 1: Add API helper**

`frontend/src/api.js`:

```javascript
export function generateInterpretHtml(id) {
  return request(`/api/tasks/${id}/actions/generate-interpret-html`, { method: 'POST' })
}
```

- [ ] **Step 2: Update TaskDetailPage imports**

Add `generateInterpretHtml` to import from `../api`.

- [ ] **Step 3: Extend polling condition**

In `useEffect` poll block, also poll when `readiness.interpret_html_lane_active`:

```javascript
const shouldPoll =
  POLL_STATUSES.has(task.status) && ... ||
  readiness.interpret_html_lane_active
```

- [ ] **Step 4: Replace header button block**

Remove:

```javascript
const canDownloadInterpret = Boolean(task.interpret_html_path || task.interpret_markdown)
...
{canDownloadInterpret && (
  <a className="btn btn-secondary" href={interpretHtmlUrl(task.id)}>
    下载解读报告
  </a>
)}
```

Add (inside `page-header-actions`):

```jsx
{task.interpret_markdown && (() => {
  const readiness = task.readiness || {}
  const htmlReady = readiness.interpret_html_ready
  const htmlGenerating = readiness.interpret_html_lane_active
  const htmlError = readiness.interpret_html_error

  if (htmlGenerating) {
    return <button type="button" className="btn btn-secondary" disabled>HTML 生成中…</button>
  }
  if (htmlReady) {
    return (
      <>
        <a className="btn btn-primary" href={interpretHtmlUrl(task.id)} download>
          直接下载
        </a>
        <button
          type="button"
          className="btn btn-secondary"
          disabled={Boolean(actionLoading)}
          onClick={() => {
            if (!window.confirm('将覆盖已生成的 HTML 报告，是否继续？')) return
            runAction('interpret-html', () => generateInterpretHtml(id))
          }}
        >
          {actionLoading === 'interpret-html' ? '提交中…' : '重新生成'}
        </button>
      </>
    )
  }
  return (
    <>
      <button
        type="button"
        className="btn btn-secondary"
        disabled={Boolean(actionLoading)}
        onClick={() => runAction('interpret-html', () => generateInterpretHtml(id))}
      >
        {actionLoading === 'interpret-html' ? '提交中…' : '下载解读报告'}
      </button>
      {htmlError && <span className="page-error">{htmlError}</span>}
    </>
  )
})()}
```

- [ ] **Step 5: Manual smoke test**

1. 打开有解读报告的任务详情
2. 点击「下载解读报告」→ 显示「HTML 生成中…」
3. 完成后出现「直接下载」「重新生成」
4. 重新生成 confirm 后再次生成

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api.js frontend/src/pages/TaskDetailPage.jsx
git commit -m "feat: upgrade interpret HTML download button with async generation UI"
```

---

### Task 9: 全量回归

- [ ] **Step 1: Run backend suite**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest -q`

Expected: all pass

- [ ] **Step 2: Fix any regressions**

重点：`test_scheduler.py`, `test_report.py`, `conftest.py` stub 路径。

- [ ] **Step 3: Final commit if fixes needed**

```bash
git commit -m "test: fix regressions after interpret HTML on-demand flow"
```

---

## Spec Coverage Checklist

| Spec § | Task |
|--------|------|
| 解读只存 Markdown | Task 3 |
| JSON schema | Task 1 |
| 固定模板渲染 | Task 2 |
| 智能体 Agent OS | Task 4, 7 |
| 异步 service | Task 5 |
| readiness 三字段 | Task 5 |
| POST/GET API | Task 6 |
| 页头按钮状态机 | Task 8 |
| 重新生成 confirm | Task 8 |
| 错误保留旧 HTML | Task 5 `_run` 不写 path on failure |
| 测试计划 | Tasks 1–6, 9 |

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-23-interpret-html-report.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每个 Task 派 fresh subagent，任务间 review，迭代快
2. **Inline Execution** — 本会话按 Task 顺序直接实现，批次间 checkpoint review

**Which approach?**
