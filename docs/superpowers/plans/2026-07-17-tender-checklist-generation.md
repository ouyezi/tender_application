# 招标诊断检查项生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在解读成功后增加任务级检查项生成阶段，任务详情展示「解读报告 → 检查项报告 → 诊断报告」，并按动态分类批量执行 Mock 诊断。

**Architecture:** 解读完成后进入 `generating_checklist`：等待招标正文解析成功 → `ChecklistContextBuilder` 组装输入 → `ChecklistAgent`（Mock）生成 → `ChecklistService` 校验并原子写入三层表 → 调度器按分类调用 `MockBatchDiagnosisEngine`（`RetrievalProvider` 本期 Mock）。检查项通过独立 API 按需加载。

**Tech Stack:** 现有 FastAPI + SQLAlchemy + asyncio scheduler；React + Vite；新增 Protocol/Mock，不接入真实 LLM。

**Spec:** `docs/superpowers/specs/2026-07-17-tender-checklist-generation-design.md`

---

## File Structure

```text
backend/app/
  config.py                              # + checklist token/delay/agent knobs
  models.py                              # + Checklist* tables, task/result fields
  schemas.py                             # + Checklist* Out, ResultOut extensions
  db.py                                  # recover generating_checklist
  engine/
    base.py                              # + Checklist*, BatchDiagnosis*, Retrieval* types
    checklist_mock.py                    # NEW MockChecklistAgent
    batch_diagnosis_mock.py              # NEW MockBatchDiagnosisEngine
    retrieval_mock.py                    # NEW MockRetrievalProvider
  services/
    checklist_context.py                 # NEW ContextBuilder + chunking
    checklist_validate.py                # NEW schema validation
    checklist_service.py                 # NEW generate/persist/retry/artifact
    scheduler.py                         # insert generating_checklist + batch diagnose
    report.py                            # two-dimension result labels in markdown
  api/
    tasks.py                             # GET checklist, POST retry; stoppable statuses

backend/tests/
  test_checklist_context.py              # NEW short/long + prefix stability
  test_checklist_agent.py                # NEW mock agent
  test_checklist_validate.py             # NEW validation failures
  test_checklist_service.py              # NEW persist + artifact
  test_batch_diagnosis.py                # NEW batch mapping rules
  test_checklist_api.py                  # NEW GET/retry
  test_scheduler.py                      # update pipeline statuses
  test_migrate_schema.py                 # + new columns/tables
  conftest.py                            # monkeypatch checklist delays/thresholds

frontend/src/
  api.js                                 # getChecklist, retryChecklist
  components/ChecklistReport.jsx         # NEW tab content
  components/ResultTable.jsx             # compliance + consequence display
  pages/TaskDetailPage.jsx               # third tab + status labels
  pages/admin/AdminTasksPage.jsx         # generating_checklist badge
  components/TaskCard.jsx                # status label
  App.css                                # checklist tab / expand styles
```

---

### Task 1: Config、模型与迁移恢复

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/db.py`
- Test: `backend/tests/test_migrate_schema.py`

- [ ] **Step 1: 追加配置项**

在 `backend/app/config.py` 末尾追加：

```python
MOCK_CHECKLIST_DELAY_SECONDS = 0.3
CHECKLIST_AGENT = "mock"  # mock | http (http not implemented this sprint)
CHECKLIST_AGENT_URL = ""
CHECKLIST_SCHEMA_VERSION = "1.0"
# Approx chars-as-tokens for demo; real tokenizer can replace later
CHECKLIST_SINGLE_PASS_TOKEN_THRESHOLD = 6000
CHECKLIST_CHUNK_TOKEN_SIZE = 2500
CHECKLIST_CHUNK_OVERLAP_TOKENS = 200
CHECKLIST_MAX_ITEMS_PER_CATEGORY = 12
```

- [ ] **Step 2: 扩展模型**

在 `backend/app/models.py` 的 `DiagnosisTask` 中，于 `interpret_html_path` 后增加：

```python
    current_checklist_generation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    failure_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
```

在 `DiagnosisResult` 中增加（保留旧字段以兼容）：

```python
    checklist_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    consequence_tags: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
```

在文件末尾（`ParseJob` 之后）新增三个模型：

```python
class ChecklistGeneration(Base):
    __tablename__ = "checklist_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), ForeignKey("diagnosis_tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generating")
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    agent_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    admin_config_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    raw_response_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    categories: Mapped[list["ChecklistCategory"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )
    items: Mapped[list["ChecklistItem"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan"
    )


class ChecklistCategory(Base):
    __tablename__ = "checklist_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    retrieval_query: Mapped[str] = mapped_column(Text, default="")
    expected_locations: Mapped[str] = mapped_column(Text, default="[]")  # JSON list
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    generation: Mapped["ChecklistGeneration"] = relationship(back_populates="categories")
    items: Mapped[list["ChecklistItem"]] = relationship(back_populates="category")


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_categories.id"), nullable=False, index=True
    )
    temp_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False, default="")
    technique: Mapped[str] = mapped_column(Text, nullable=False, default="")
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    source_references: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    retrieval_hints: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    expected_evidence: Mapped[str] = mapped_column(Text, default="")
    compliance_rules: Mapped[str] = mapped_column(Text, default="")
    consequence_rules: Mapped[str] = mapped_column(Text, default="")
    admin_config_refs: Mapped[str] = mapped_column(Text, default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    generation: Mapped["ChecklistGeneration"] = relationship(back_populates="items")
    category: Mapped["ChecklistCategory"] = relationship(back_populates="items")
```

- [ ] **Step 3: 恢复逻辑加入新状态**

在 `backend/app/db.py` 的 `recover_interrupted_tasks` 中，把状态列表改为：

```python
["interpreting", "generating_checklist", "diagnosing", "running", "paused"]
```

同时在 `scheduler.py` 的 `STOPPABLE_STATUSES`（Task 7 会改）先记下需要包含 `generating_checklist`。

- [ ] **Step 4: 迁移测试**

在 `backend/tests/test_migrate_schema.py` 追加测试：创建仅含旧列的 `diagnosis_tasks` 表后调用 `init_db_on_connection`，断言存在 `current_checklist_generation_id`、`failure_stage`，且 `checklist_generations` / `checklist_categories` / `checklist_items` 表已创建。

- [ ] **Step 5: 运行测试并提交**

```bash
cd /Users/tongqianni/xlab/tender_application/backend
../.venv/bin/python -m pytest tests/test_migrate_schema.py -v
```

Expected: PASS

```bash
cd /Users/tongqianni/xlab/tender_application
git add backend/app/config.py backend/app/models.py backend/app/db.py backend/tests/test_migrate_schema.py
git commit -m "feat: add checklist generation models and config"
```

---

### Task 2: Engine 协议、Mock Agent、校验与上下文构建

**Files:**
- Modify: `backend/app/engine/base.py`
- Create: `backend/app/engine/checklist_mock.py`
- Create: `backend/app/services/checklist_context.py`
- Create: `backend/app/services/checklist_validate.py`
- Create: `backend/tests/test_checklist_agent.py`
- Create: `backend/tests/test_checklist_context.py`
- Create: `backend/tests/test_checklist_validate.py`

- [ ] **Step 1: 在 `base.py` 追加类型与协议**

```python
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class TenderChunk:
    chunk_id: str
    text: str
    chapter: str = ""
    start_offset: int = 0
    end_offset: int = 0


@dataclass
class ChecklistSourceRef:
    chapter: str
    quote: str
    start_offset: int = 0
    end_offset: int = 0


@dataclass
class ChecklistItemDraft:
    temp_id: str
    title: str
    requirement: str
    technique: str
    importance: str
    category_temp_id: str
    source_references: list[ChecklistSourceRef]
    retrieval_hints: list[str] = field(default_factory=list)
    expected_evidence: str = ""
    compliance_rules: str = ""
    consequence_rules: str = ""
    admin_config_refs: list[int] = field(default_factory=list)


@dataclass
class ChecklistCategoryDraft:
    temp_id: str
    name: str
    description: str = ""
    retrieval_query: str = ""
    expected_locations: list[str] = field(default_factory=list)


@dataclass
class ChecklistAgentResult:
    schema_version: str
    categories: list[ChecklistCategoryDraft]
    items: list[ChecklistItemDraft]
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChecklistAgentInput:
    task_id: str
    interpret_markdown: str
    admin_configs: list[dict[str, Any]]
    tender_chunks: list[TenderChunk]
    single_pass: bool
    stable_prefix: str


class ChecklistAgent(Protocol):
    async def generate(self, payload: ChecklistAgentInput) -> ChecklistAgentResult: ...


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    location: str = ""


class RetrievalProvider(Protocol):
    async def retrieve_for_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[RetrievedChunk]: ...


@dataclass
class BatchItemResult:
    checklist_item_id: int
    compliance: str  # satisfied | violated | cannot_satisfy | insufficient_evidence
    consequence_tags: list[str]
    evidence: str
    suggestion: str
    description: str = ""


class BatchDiagnosisEngine(Protocol):
    async def diagnose_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[BatchItemResult]: ...
```

保留现有 `DiagnosisItemResult` / `DiagnosisEngine` / `Interpretation*` 不动（旧单测仍可用；调度器将切到批量路径）。

- [ ] **Step 2: 写失败测试 — Mock Agent**

`backend/tests/test_checklist_agent.py`:

```python
import pytest
from app.engine.base import ChecklistAgentInput, TenderChunk
from app.engine.checklist_mock import MockChecklistAgent


@pytest.mark.asyncio
async def test_mock_checklist_agent_returns_categories_and_items():
    agent = MockChecklistAgent(delay_seconds=0)
    payload = ChecklistAgentInput(
        task_id="T-1",
        interpret_markdown="# 解读\n资质要求：具备建筑工程施工总承包一级资质。",
        admin_configs=[{"id": 1, "title": "企业资质核验"}],
        tender_chunks=[
            TenderChunk("c1", "## 资格要求\n投标人须具备建筑工程施工总承包一级资质。", "资格要求", 0, 40)
        ],
        single_pass=True,
        stable_prefix="PREFIX",
    )
    result = await agent.generate(payload)
    assert result.schema_version == "1.0"
    assert result.categories
    assert result.items
    assert all(i.source_references for i in result.items)
    assert all(i.category_temp_id for i in result.items)
```

- [ ] **Step 3: 实现 `MockChecklistAgent`**

`backend/app/engine/checklist_mock.py`：短延迟后返回确定性 2 个分类、至少 3 个检查项（资质、评分、废标各一类要点）。`temp_id` 固定为 `cat-1`/`item-1` 等；`source_references` 引用输入 chunk；`importance` 使用 `high|medium|low`；填写 `compliance_rules` / `consequence_rules` / `retrieval_hints`。长文档多 chunk 时对每个 chunk 先产候选再在 `generate` 内合并去重（按 title 规范化）。

- [ ] **Step 4: Context builder 测试**

`backend/tests/test_checklist_context.py`:

```python
from app.services.checklist_context import (
    build_stable_prefix,
    estimate_tokens,
    split_tender_text,
    should_single_pass,
)


def test_estimate_and_single_pass_threshold(monkeypatch):
    monkeypatch.setattr("app.services.checklist_context.CHECKLIST_SINGLE_PASS_TOKEN_THRESHOLD", 100)
    assert should_single_pass("x" * 50) is True
    assert should_single_pass("x" * 400) is False


def test_split_preserves_overlap_and_chapter(monkeypatch):
    monkeypatch.setattr("app.services.checklist_context.CHECKLIST_CHUNK_TOKEN_SIZE", 40)
    monkeypatch.setattr("app.services.checklist_context.CHECKLIST_CHUNK_OVERLAP_TOKENS", 5)
    text = "## A\n" + ("甲" * 30) + "\n## B\n" + ("乙" * 30)
    chunks = split_tender_text(text)
    assert len(chunks) >= 2
    assert chunks[0].chapter


def test_stable_prefix_identical_across_chunks():
    p1 = build_stable_prefix("# 解读", [{"id": 1, "title": "资质"}])
    p2 = build_stable_prefix("# 解读", [{"id": 1, "title": "资质"}])
    assert p1 == p2
    assert p1.startswith("SYSTEM:")
```

- [ ] **Step 5: 实现 `checklist_context.py`**

```python
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import (
    CHECKLIST_CHUNK_OVERLAP_TOKENS,
    CHECKLIST_CHUNK_TOKEN_SIZE,
    CHECKLIST_SINGLE_PASS_TOKEN_THRESHOLD,
)
from app.engine.base import ChecklistAgentInput, TenderChunk


def estimate_tokens(text: str) -> int:
    # Demo heuristic: CJK ~1 token/char, latin ~0.25
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def should_single_pass(text: str) -> bool:
    return estimate_tokens(text) <= CHECKLIST_SINGLE_PASS_TOKEN_THRESHOLD


def build_stable_prefix(interpret_markdown: str, admin_configs: list[dict[str, Any]]) -> str:
    return (
        "SYSTEM: checklist schema v1.0\n"
        "OUTPUT: categories+items JSON\n"
        "---INTERPRET---\n"
        f"{interpret_markdown}\n"
        "---ADMIN_CONFIG---\n"
        f"{json.dumps(admin_configs, ensure_ascii=False)}\n"
    )


def split_tender_text(text: str) -> list[TenderChunk]:
    # Prefer ##/### chapter splits; then token-size slices with overlap
    ...
```

实现要点：
- `split_tender_text`：用 `re.split(r'(?=^#{2,3}\s)', text, flags=re.M)` 分章；章内超长再按 `CHECKLIST_CHUNK_TOKEN_SIZE` 切片，重叠 `CHECKLIST_CHUNK_OVERLAP_TOKENS`。
- `load_tender_markdown(task_id, session)`：读取任务 `tender_file_id` 对应 `WorkspaceFile.md_path`；不存在或 `parse_status` 不是 `succeeded`/`partial` 时抛明确异常（`partial` 在调度层按规格：本期解析失败策略为 block——仅 `succeeded` 放行；`partial` 也阻止并要求重试解析，与「不静默降级」一致）。
- `build_agent_input(...)` 返回 `ChecklistAgentInput`。

- [ ] **Step 6: 校验器测试与实现**

`backend/tests/test_checklist_validate.py`：覆盖缺字段、非法 importance、未知 category_temp_id、重复 temp_id、空 source_references、空 retrieval_query、分类超 `CHECKLIST_MAX_ITEMS_PER_CATEGORY`。

`backend/app/services/checklist_validate.py`：

```python
def validate_checklist_result(result: ChecklistAgentResult) -> list[str]:
    """Return list of error messages; empty means OK."""
```

- [ ] **Step 7: 跑测试并提交**

```bash
cd /Users/tongqianni/xlab/tender_application/backend
../.venv/bin/python -m pytest tests/test_checklist_agent.py tests/test_checklist_context.py tests/test_checklist_validate.py -v
```

Expected: PASS

```bash
git add backend/app/engine/base.py backend/app/engine/checklist_mock.py \
  backend/app/services/checklist_context.py backend/app/services/checklist_validate.py \
  backend/tests/test_checklist_agent.py backend/tests/test_checklist_context.py \
  backend/tests/test_checklist_validate.py
git commit -m "feat: add checklist agent protocol, mock, context, and validation"
```

---

### Task 3: ChecklistService 持久化与 Artifact

**Files:**
- Create: `backend/app/services/checklist_service.py`
- Create: `backend/tests/test_checklist_service.py`
- Modify: `backend/tests/conftest.py`（monkeypatch `REPORT_DIR` / checklist delay 如需）

- [ ] **Step 1: 失败测试**

```python
import json
import pytest
from sqlalchemy import select
from app.models import ChecklistGeneration, ChecklistItem, DiagnosisTask
from app.services import checklist_service


@pytest.mark.asyncio
async def test_generate_and_persist_success(client, tmp_path, monkeypatch):
    # create task via API with configs + tiny files (reuse helpers from test_tasks)
    # force tender parse succeeded by writing md_path on WorkspaceFile
    # call checklist_service.generate_for_task(task_id)
    # assert generation succeeded, categories/items count > 0
    # assert task.current_checklist_generation_id set
    # assert artifact JSON exists under uploads/{task_id}/report/checklist.json
    ...


@pytest.mark.asyncio
async def test_validation_failure_does_not_switch_current(client):
    # monkeypatch agent to return invalid payload
    # assert generation.status == failed, task.current_checklist_generation_id is None
    # assert no ChecklistItem rows for that generation OR generation failed with zero items
    ...
```

- [ ] **Step 2: 实现 `checklist_service.py`**

核心 API：

```python
async def generate_for_task(task_id: str) -> ChecklistGeneration:
    """Create generation row → build input → agent → validate → atomic persist → set current id."""

async def get_checklist_payload(task_id: str) -> dict:
    """Load current generation with nested categories/items + summary counts."""

async def wait_for_tender_parse_ready(task_id: str, timeout: float = 30.0) -> None:
    """Poll WorkspaceFile until succeeded; failed/partial raises ChecklistBlockedError."""
```

原子写入规则：
1. 插入 `ChecklistGeneration(status=generating)`。
2. 调用 agent；原始 JSON 写到 `reports/{task_id}/checklist_raw_{generation_id}.json`。
3. `validate_checklist_result`；失败则 `status=failed`、写 `error_message`、`task.failure_stage="checklist_validation"`，**不**设置 `current_checklist_generation_id`。
4. 成功：先写 categories，再写 items（映射 temp_id → DB id），再 `status=succeeded`，最后设置 `task.current_checklist_generation_id` 并清空 `failure_stage`。
5. `artifact.sync_to_artifact_report` 同步 `checklist.json`（结构化导出）。

- [ ] **Step 3: 跑测试并提交**

```bash
../.venv/bin/python -m pytest tests/test_checklist_service.py -v
git add backend/app/services/checklist_service.py backend/tests/test_checklist_service.py backend/tests/conftest.py
git commit -m "feat: persist checklist generations with atomic promotion"
```

---

### Task 4: RetrievalProvider + BatchDiagnosisEngine

**Files:**
- Create: `backend/app/engine/retrieval_mock.py`
- Create: `backend/app/engine/batch_diagnosis_mock.py`
- Create: `backend/tests/test_batch_diagnosis.py`

- [ ] **Step 1: 失败测试**

```python
import pytest
from app.engine.batch_diagnosis_mock import MockBatchDiagnosisEngine
from app.engine.retrieval_mock import MockRetrievalProvider


@pytest.mark.asyncio
async def test_mock_retrieval_returns_chunks():
    provider = MockRetrievalProvider()
    chunks = await provider.retrieve_for_category(
        task_id="T-1",
        category={"id": 1, "name": "资格", "retrieval_query": "资质"},
        items=[{"id": 10, "title": "一级资质", "retrieval_hints": ["资质证书"]}],
    )
    assert len(chunks) >= 1


@pytest.mark.asyncio
async def test_batch_returns_exact_item_ids():
    engine = MockBatchDiagnosisEngine(delay_seconds=0)
    items = [
        {"id": 1, "title": "A", "requirement": "r", "importance": "high"},
        {"id": 2, "title": "B", "requirement": "r", "importance": "low"},
    ]
    results = await engine.diagnose_category(
        task_id="T-1",
        category={"id": 1, "name": "资格"},
        items=items,
        retrieved_chunks=[],
    )
    assert {r.checklist_item_id for r in results} == {1, 2}
    assert all(r.compliance in {"satisfied", "violated", "cannot_satisfy", "insufficient_evidence"} for r in results)


@pytest.mark.asyncio
async def test_batch_rejects_incomplete_mapping():
    class BadEngine(MockBatchDiagnosisEngine):
        async def diagnose_category(self, **kwargs):
            results = await super().diagnose_category(**kwargs)
            return results[:-1]  # drop one

    with pytest.raises(ValueError, match="mapping"):
        engine = BadEngine(delay_seconds=0)
        items = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
        results = await engine.diagnose_category(
            task_id="T-1", category={"id": 1, "name": "x"}, items=items, retrieved_chunks=[]
        )
        # helper used by scheduler:
        from app.services.checklist_service import assert_batch_complete
        assert_batch_complete(items, results)
```

把 `assert_batch_complete(items, results)` 放在 `checklist_service.py` 或新建 `batch_diagnosis.py` 小工具：检查 ID 集合相等且无重复。

- [ ] **Step 2: 实现 Mock**

- `MockRetrievalProvider`：返回 2 个固定 `RetrievedChunk`，文本含分类名。
- `MockBatchDiagnosisEngine`：按 `item["id"]` 哈希选择 compliance 与 consequence_tags；`description` 可用 requirement 摘要。

- [ ] **Step 3: 提交**

```bash
../.venv/bin/python -m pytest tests/test_batch_diagnosis.py -v
git add backend/app/engine/retrieval_mock.py backend/app/engine/batch_diagnosis_mock.py \
  backend/tests/test_batch_diagnosis.py backend/app/services/checklist_service.py
git commit -m "feat: add mock retrieval and batch diagnosis engine"
```

---

### Task 5: 调度器接入检查项生成与分类批诊断

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/app/services/report.py`（结论展示适配新枚举）

- [ ] **Step 1: 更新调度器常量**

```python
STOPPABLE_STATUSES = frozenset(
    {"interpreting", "generating_checklist", "diagnosing", "running", "paused"}
)
```

- [ ] **Step 2: 改写 `_run` 阶段顺序**

解读成功并落盘后：

```python
task.status = "generating_checklist"
task.failure_stage = None
# wait tender parse ready
await checklist_service.wait_for_tender_parse_ready(task_id)
if _should_stop(...): ...
await checklist_service.generate_for_task(task_id)
# reload item count
progress_total = count items of current generation
task.progress_total = progress_total
task.progress_done = 0
task.status = "diagnosing"
```

诊断循环改为按分类：

```python
categories = load categories ordered by sort_order
done = 0
for category in categories:
    await _wait_if_paused(...)
    if _should_stop(...): ...
    items = load items for category
    chunks = await retrieval.retrieve_for_category(...)
    batch = await batch_engine.diagnose_category(...)
    assert_batch_complete(items, batch)
    # persist DiagnosisResult rows:
    # content_title=item.title
    # description=batch.description or item.requirement
    # result=compliance
    # consequence_tags=json.dumps(tags)
    # checklist_item_id=item.id
    # evidence/suggestion from batch
    done += len(items)
    task.progress_done = done
```

注意：
- 创建任务时仍可写 `config_snapshot` 供审计，但 `progress_total` 在检查项生成成功后覆盖为检查项数量。
- `resume` 仍仅 `paused` → `diagnosing`；若进程中断后不自动恢复生成（与现有一致 → `stopped`）。
- 解析失败：`task.status=failed`，`failure_stage="tender_parse"`，`error_message` 明确。
- 检查项失败：`failure_stage="checklist_generation"` 或 `checklist_validation`。

- [ ] **Step 3: 更新 `report.build_markdown`**

概览统计适配新 compliance 枚举（中文标签映射）：

```python
COMPLIANCE_LABELS = {
    "satisfied": "满足",
    "violated": "违反",
    "cannot_satisfy": "不能满足",
    "insufficient_evidence": "证据不足",
    # legacy mock labels still counted if present
}
```

明细中增加后果标签行（从 `consequence_tags` JSON 解析）。

- [ ] **Step 4: 更新 scheduler 测试**

覆盖：
1. 全流程：`interpreting` → `generating_checklist` → `diagnosing` → `completed`，且 `current_checklist_generation_id` 非空，results 含 `checklist_item_id`。
2. 解析失败：将 tender `WorkspaceFile.parse_status=failed`，任务 `failed`，`failure_stage=tender_parse`，无 current generation。
3. pause 在 `generating_checklist` → 409（pause API 仍只允许 diagnosing）。
4. stop 在 `generating_checklist` → `stopped`。
5. `progress_total` 等于检查项数而非旧 config 数（可 seed 多条 config 对比）。

为加速测试：在 conftest monkeypatch `MOCK_CHECKLIST_DELAY_SECONDS=0`，并在创建任务后把 tender 文件 `parse_status` 设为 `succeeded`、写入临时 `md_path`（或在 `wait_for_tender_parse_ready` 测试里用短 timeout + 后台把状态改成功）。

推荐测试 helper：

```python
async def _mark_tender_parsed(session_factory, task_id: str, md_text: str):
    # set WorkspaceFile for tender_file_id to succeeded + write md file
```

- [ ] **Step 5: 跑相关测试并提交**

```bash
../.venv/bin/python -m pytest tests/test_scheduler.py tests/test_report.py tests/test_tasks.py -v
```

修复因状态/结果字段变更导致的旧断言。

```bash
git add backend/app/services/scheduler.py backend/app/services/report.py \
  backend/tests/test_scheduler.py backend/tests/test_report.py backend/tests/conftest.py \
  backend/tests/test_tasks.py
git commit -m "feat: schedule checklist generation and category-batch diagnosis"
```

---

### Task 6: Checklist API 与 Schemas

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/tasks.py`
- Create: `backend/tests/test_checklist_api.py`

- [ ] **Step 1: Schemas**

```python
class ChecklistItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    requirement: str
    technique: str
    importance: str
    category_id: int
    category_name: str = ""
    source_references: list[Any] = []
    retrieval_hints: list[Any] = []
    expected_evidence: str = ""
    compliance_rules: str = ""
    consequence_rules: str = ""
    admin_config_refs: list[Any] = []
    sort_order: int


class ChecklistCategoryOut(BaseModel):
    id: int
    name: str
    description: str
    retrieval_query: str
    expected_locations: list[Any] = []
    sort_order: int
    items: list[ChecklistItemOut] = []


class ChecklistSummaryOut(BaseModel):
    item_count: int
    category_count: int
    importance_counts: dict[str, int]


class ChecklistOut(BaseModel):
    task_id: str
    generation_id: Optional[int]
    status: str  # none | generating | succeeded | failed
    failure_stage: Optional[str] = None
    error_message: Optional[str] = None
    summary: ChecklistSummaryOut
    categories: list[ChecklistCategoryOut] = []
```

扩展 `ResultOut`：

```python
    checklist_item_id: Optional[int] = None
    consequence_tags: list[str] = []
```

在 `tasks.py` 的序列化处把 `consequence_tags` 从 JSON 字符串解析为 list。

`TaskListOut`/`TaskOut` 增加可选：

```python
    failure_stage: Optional[str] = None
    current_checklist_generation_id: Optional[int] = None
```

- [ ] **Step 2: API**

```python
@router.get("/{task_id}/checklist", response_model=ChecklistOut)
async def get_checklist(task_id: str, db: AsyncSession = Depends(get_db)):
    ...

@router.post("/{task_id}/checklist/retry", response_model=TaskOut)
async def retry_checklist(task_id: str, db: AsyncSession = Depends(get_db)):
    # 404 if task missing
    # 409 if status not failed with failure_stage in {tender_parse, checklist_generation, checklist_validation}
    #    OR if diagnosing already started (results exist / status diagnosing|completed)
    # 409 if tender parse not succeeded → message to reparse workspace
    # else: clear error, set generating_checklist, scheduler.start_task (skip interpret if interpret_md_path set)
```

调度器已有 `need_interpret = not task.interpret_md_path`，retry 只需把状态设为 `generating_checklist` 并 `start_task`。

- [ ] **Step 3: API 测试**

- GET 无清单：`status=none`，空 categories。
- 完成后 GET：含 summary 与 items。
- 解析失败后 retry → 409。
- 生成失败且 parse ok → retry → 最终 succeeded。
- diagnosing 中 retry → 409。

- [ ] **Step 4: 提交**

```bash
../.venv/bin/python -m pytest tests/test_checklist_api.py tests/test_tasks.py -v
git add backend/app/schemas.py backend/app/api/tasks.py backend/tests/test_checklist_api.py
git commit -m "feat: add checklist query and retry API"
```

---

### Task 7: 前端三 Tab 与检查项报告

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/ChecklistReport.jsx`
- Modify: `frontend/src/pages/TaskDetailPage.jsx`
- Modify: `frontend/src/components/ResultTable.jsx`
- Modify: `frontend/src/components/TaskCard.jsx`
- Modify: `frontend/src/pages/admin/AdminTasksPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: API helpers**

```js
export function getChecklist(taskId) {
  return request(`/api/tasks/${taskId}/checklist`)
}

export function retryChecklist(taskId) {
  return request(`/api/tasks/${taskId}/checklist/retry`, { method: 'POST' })
}
```

- [ ] **Step 2: `ChecklistReport.jsx`**

Props: `taskId`, `taskStatus`, `failureStage`, `errorMessage`。

行为：
- mount / `taskStatus` 变化时 `getChecklist`。
- 顶部展示 status、item_count、category_count、importance_counts。
- 分类 chips 筛选（含「全部」）。
- 表格列：诊断标题、诊断要求、诊断技巧、重要性、分类；行点击展开机器字段。
- `generating_checklist`：显示「检查项生成中…」。
- `failure_stage === 'tender_parse'`：提示前往 `/workspaces/{taskId}` 重新解析。
- 其他生成失败：显示错误 +「重试生成」按钮调用 `retryChecklist`。
- `status === 'none'`：旧任务空态「暂无检查项报告」。

- [ ] **Step 3: `TaskDetailPage.jsx`**

- `STATUS_LABELS` 增加 `generating_checklist: '生成检查项'`。
- `POLL_STATUSES` 加入 `generating_checklist`。
- Tab 顺序：`interpret` → `checklist` → `diagnosis`。
- 渲染 `ChecklistReport`。

- [ ] **Step 4: `ResultTable.jsx`**

- 结果列映射 compliance 中文。
- 增加「后果」列：解析 `consequence_tags`（若为字符串则 `JSON.parse` 容错）。

```js
const COMPLIANCE_LABELS = {
  satisfied: '满足',
  violated: '违反',
  cannot_satisfy: '不能满足',
  insufficient_evidence: '证据不足',
  通过: '通过',
  风险: '风险',
  缺失: '缺失',
}
```

- [ ] **Step 5: 列表/管理端状态徽章**

`TaskCard.jsx` 与 `AdminTasksPage.jsx` 同步 `generating_checklist` 文案；管理端进度在该状态可显示「检查项生成中」。

- [ ] **Step 6: 样式**

复用 `.report-tabs`；新增 `.checklist-summary`、`.checklist-filters`、`.checklist-expand`，避免卡片堆叠与紫色渐变。

- [ ] **Step 7: 手动冒烟后提交**

```bash
# optional: npm run build in frontend if available
cd /Users/tongqianni/xlab/tender_application
git add frontend
git commit -m "feat: show checklist report tab between interpret and diagnosis"
```

---

### Task 8: 全量回归与 README 验收补充

**Files:**
- Modify: `README.md`
- Possibly fix any remaining test failures

- [ ] **Step 1: 跑全量后端测试**

```bash
cd /Users/tongqianni/xlab/tender_application/backend
../.venv/bin/python -m pytest -v
```

Expected: 全部 PASS。

- [ ] **Step 2: README 验收清单追加**

在验收清单增加：

```markdown
9. **检查项生成**：创建任务后状态经过「解读中 → 生成检查项 → 诊断中 → 已完成」；详情第三 Tab「检查项报告」展示标题/要求/技巧/重要性/分类，可展开机器字段。
10. **解析失败不降级**：人为使招标解析失败时任务失败且无正式检查项；工作区重试解析成功后可「重试生成」。
11. **分类批诊断**：诊断结果条数等于检查项数；结果含符合性与后果标签。
```

说明更新：诊断引擎现为「检查项生成 Mock + 分类批量 Mock」，不再用创建时全局配置作为执行清单。

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: document checklist generation acceptance"
```

---

## Spec Coverage Self-Review

| Spec 要求 | Task |
|---|---|
| 输入：正文 + 解读 + 管理配置软参考 | 2, 3, 5 |
| 三层表 + current_checklist_generation_id | 1, 3 |
| 动态分类作批诊断边界 | 4, 5 |
| 短单次 / 长分片 + 稳定前缀 | 2 |
| 校验失败不写半成品 / 可重试 | 3, 6 |
| 解析失败阻止生成 | 5, 6 |
| GET checklist + retry API | 6 |
| 三 Tab UI | 7 |
| Mock 分类批诊断 + RetrievalProvider | 4, 5 |
| 符合性 + 后果两维结果 | 4, 5, 7 |
| 迁移 / 旧任务兼容 / 恢复 | 1, 5, 7 |
| 报告与进度来自检查项 | 5 |

## Placeholder / consistency check

- 状态字符串统一：`generating_checklist`
- Agent 草稿用 `temp_id`；DB 用整型 `id`；诊断结果用 `checklist_item_id`
- Compliance 枚举：`satisfied|violated|cannot_satisfy|insufficient_evidence`
- Consequence：`no_score|bid_unusable|score_risk|general_risk`
- `partial` 解析视为未就绪（阻止生成），与「不静默降级」一致
- 无 TBD/TODO 步骤

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-tender-checklist-generation.md`.

**Two execution options:**

1. **Subagent-Driven（推荐）** — 每个 Task 派一个新子代理，Task 之间做审查，迭代快  
2. **Inline Execution** — 在本会话用 executing-plans 按批次执行并设检查点  

选哪种方式？
