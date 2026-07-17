# 招标诊断检查项生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有解读与诊断之间生成任务级检查项清单，按动态分类执行批量 Mock 诊断，并在任务详情新增“检查项报告”Tab。

**Architecture:** 新增检查项生成记录、动态分类和检查项三层持久化模型；`ChecklistContextBuilder` 从工作区已解析的招标正文构造缓存友好的长短文档输入，`ChecklistAgent` 生成结构化清单，`ChecklistService` 校验并原子发布。调度器只编排 `interpreting → generating_checklist → diagnosing`，诊断通过分类级 `BatchDiagnosisEngine` 执行，并为后续真实工作区召回保留 `RetrievalProvider` 边界。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Pydantic 2、SQLite、pytest；React 19、Vite 8、Vitest、Testing Library、oxlint。

**Spec:** `docs/superpowers/specs/2026-07-17-tender-checklist-generation-design.md`

---

## File Structure

### Backend

- Modify `backend/app/models.py` — 新增检查项三层模型、任务当前版本指针和诊断结果结构化字段。
- Modify `backend/app/db.py` — 恢复 `generating_checklist` 状态并沿用轻量迁移。
- Modify `backend/app/config.py` — 增加生成阈值、分片和分类上限配置。
- Modify `backend/app/schemas.py` — 增加检查项嵌套 API Schema 与结果字段。
- Modify `backend/app/engine/base.py` — 增加检查项 Agent、批量诊断和召回协议。
- Create `backend/app/engine/checklist_mock.py` — 确定性检查项 Mock Agent。
- Create `backend/app/engine/batch_mock.py` — 确定性分类批诊断与 Mock 召回。
- Create `backend/app/services/checklist_context.py` — 等待/读取招标解析产物，按章节和 token 构造稳定前缀分片。
- Create `backend/app/services/checklist_service.py` — 调用 Agent、严格校验、持久化、Artifact 同步和查询。
- Create `backend/app/services/diagnosis_service.py` — 分类批诊断、完整映射校验和整批写入。
- Modify `backend/app/services/artifact.py` — 写入检查项原始响应与正式 JSON。
- Modify `backend/app/services/scheduler.py` — 接入检查项阶段、分类批诊断和重试。
- Modify `backend/app/api/tasks.py` — 新增清单查询与重试 API，创建任务时诊断总数改为 0。
- Modify `backend/app/services/report.py` — 报告展示符合性和后果标签。
- Modify `backend/tests/conftest.py` — 配置 Mock 延迟和生成阈值。
- Create `backend/tests/test_checklist_context.py`
- Create `backend/tests/test_checklist_agent.py`
- Create `backend/tests/test_checklist_service.py`
- Create `backend/tests/test_batch_diagnosis.py`
- Create `backend/tests/test_checklist_api.py`
- Modify `backend/tests/test_db.py`
- Modify `backend/tests/test_migrate_schema.py`
- Modify `backend/tests/test_scheduler.py`
- Modify `backend/tests/test_report.py`

### Frontend

- Modify `frontend/package.json` — 增加组件测试依赖与 `test` script。
- Create `frontend/src/components/ChecklistReport.jsx` — 汇总、分类筛选、表格和明细展开。
- Create `frontend/src/components/ChecklistReport.test.jsx` — 成功、失败和空态组件测试。
- Modify `frontend/src/api.js` — 检查项查询与重试。
- Modify `frontend/src/pages/TaskDetailPage.jsx` — 三 Tab、按需加载、轮询和重试。
- Create `frontend/src/pages/TaskDetailPage.test.jsx` — Tab 顺序、按需加载和检查项状态测试。
- Modify `frontend/src/components/TaskCard.jsx` — 新状态文案。
- Modify `frontend/src/pages/admin/AdminTasksPage.jsx` — 新状态文案与停止按钮。
- Modify `frontend/src/App.css` — 检查项报告和 `generating_checklist` 状态样式。
- Modify `README.md` — 更新流水线与验收说明。

---

### Task 1: 数据模型与兼容迁移

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/db.py`
- Modify: `backend/tests/test_db.py`
- Modify: `backend/tests/test_migrate_schema.py`

- [ ] **Step 1: 写模型与恢复状态的失败测试**

在 `backend/tests/test_db.py` 增加：

```python
from sqlalchemy import select

from app.models import (
    ChecklistCategory,
    ChecklistGeneration,
    ChecklistItem,
    DiagnosisResult,
)


@pytest.mark.asyncio
async def test_persist_task_checklist_and_structured_result(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'checklist.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        task = DiagnosisTask(
            id="T-CHECKLIST-001",
            tender_filename="tender.docx",
            tender_path="/tmp/tender.docx",
            bid_filename="bid.docx",
            bid_path="/tmp/bid.docx",
            status="generating_checklist",
        )
        session.add(task)
        await session.flush()
        generation = ChecklistGeneration(
            task_id=task.id,
            status="succeeded",
            agent_type="mock",
            agent_version="1",
            schema_version="1",
            input_hash="abc",
            admin_config_snapshot="[]",
        )
        session.add(generation)
        await session.flush()
        category = ChecklistCategory(
            id="cat-qualification",
            generation_id=generation.id,
            name="资格证明材料",
            description="资格类材料",
            retrieval_query="营业执照 资质证书",
            expected_locations='["资格审查"]',
            sort_order=0,
        )
        session.add(category)
        item = ChecklistItem(
            id="item-license",
            generation_id=generation.id,
            category_id=category.id,
            title="营业执照有效性",
            requirement="营业执照须在有效期内。",
            technique="检索营业执照及有效期并交叉核对主体名称。",
            importance="high",
            source_references='[{"section":"资格要求","start":0,"end":12}]',
            retrieval_hints='["营业执照","有效期"]',
            expected_evidence='["营业执照扫描件"]',
            compliance_rules='{"satisfied":"证件有效且主体一致"}',
            consequence_rules='{"bid_unusable":"证件缺失或失效"}',
            admin_config_refs="[]",
            sort_order=0,
        )
        session.add(item)
        await session.flush()
        task.current_checklist_generation_id = generation.id
        session.add(
            DiagnosisResult(
                task_id=task.id,
                checklist_item_id=item.id,
                content_title=item.title,
                description=item.requirement,
                result="满足",
                compliance_status="satisfied",
                consequence_tags='["general_risk"]',
                evidence="已找到营业执照。",
                suggestion="无需修改。",
                sort_order=0,
            )
        )
        await session.commit()

    async with factory() as session:
        fetched = await session.get(DiagnosisTask, "T-CHECKLIST-001")
        result = (await session.execute(select(DiagnosisResult))).scalar_one()
        assert fetched.current_checklist_generation_id is not None
        assert result.checklist_item_id == "item-license"
        assert result.compliance_status == "satisfied"
        assert result.consequence_tags == '["general_risk"]'
    await engine.dispose()
```

把 `test_recover_interrupted_tasks` 的状态参数增加：

```python
("task-generating-checklist", "generating_checklist"),
```

并断言恢复后为 `stopped`。

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application/backend
../.venv/bin/python -m pytest tests/test_db.py -v
```

Expected: FAIL，提示检查项模型或新字段不存在。

- [ ] **Step 3: 实现模型**

在 `backend/app/models.py` 增加三个模型，并修改任务与结果字段：

```python
class ChecklistGeneration(Base):
    __tablename__ = "checklist_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("diagnosis_tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_config_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    raw_response_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ChecklistCategory(Base):
    __tablename__ = "checklist_categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_query: Mapped[str] = mapped_column(Text, nullable=False)
    expected_locations: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_generations.id"), nullable=False, index=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("checklist_categories.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement: Mapped[str] = mapped_column(Text, nullable=False)
    technique: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[str] = mapped_column(String(16), nullable=False)
    source_references: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_hints: Mapped[str] = mapped_column(Text, nullable=False)
    expected_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    compliance_rules: Mapped[str] = mapped_column(Text, nullable=False)
    consequence_rules: Mapped[str] = mapped_column(Text, nullable=False)
    admin_config_refs: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

在 `DiagnosisTask` 增加：

```python
current_checklist_generation_id: Mapped[Optional[int]] = mapped_column(
    ForeignKey("checklist_generations.id"), nullable=True
)
```

在 `DiagnosisResult` 增加：

```python
checklist_item_id: Mapped[Optional[str]] = mapped_column(
    ForeignKey("checklist_items.id"), nullable=True, index=True
)
compliance_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
consequence_tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
```

- [ ] **Step 4: 扩展恢复状态与迁移测试**

在 `backend/app/db.py` 的恢复集合中加入 `"generating_checklist"`：

```python
DiagnosisTask.status.in_(
    ["interpreting", "generating_checklist", "diagnosing", "running", "paused"]
)
```

在 `backend/tests/test_migrate_schema.py` 的迁移断言中加入：

```python
assert task.current_checklist_generation_id is None
assert names == {
    "workspace_files",
    "parse_jobs",
    "checklist_generations",
    "checklist_categories",
    "checklist_items",
}
```

查询 `sqlite_master` 时把三个新表加入 `IN (...)`。

- [ ] **Step 5: 运行模型和迁移测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_db.py tests/test_migrate_schema.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/models.py backend/app/db.py backend/tests/test_db.py backend/tests/test_migrate_schema.py
git commit -m "feat: add task checklist persistence models"
```

---

### Task 2: 检查项上下文与缓存友好分片

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/app/services/checklist_context.py`
- Create: `backend/tests/test_checklist_context.py`

- [ ] **Step 1: 写短文档、长文档和解析状态测试**

创建 `backend/tests/test_checklist_context.py`：

```python
import json

import pytest

from app.services.checklist_context import (
    ChecklistInputError,
    build_prompt_context,
    split_tender_markdown,
)


def test_short_document_uses_one_segment():
    segments = split_tender_markdown("# 第一章\n资格要求", threshold_tokens=100, chunk_tokens=50, overlap_tokens=5)
    assert segments == ["# 第一章\n资格要求"]


def test_long_document_splits_by_heading_and_keeps_stable_prefix():
    markdown = "# 第一章\n" + ("资格要求 " * 30) + "\n# 第二章\n" + ("评分标准 " * 30)
    context = build_prompt_context(
        tender_markdown=markdown,
        interpret_markdown="# 解读报告\n重点",
        admin_configs=[{"id": 1, "title": "资质"}],
        threshold_tokens=20,
        chunk_tokens=18,
        overlap_tokens=2,
    )
    assert len(context.segments) > 1
    assert all(call.stable_prefix == context.stable_prefix for call in context.calls)
    assert [call.tender_segment for call in context.calls] == context.segments
    assert context.stable_prefix.index("固定生成规则") < context.stable_prefix.index("完整解读报告")
    assert context.stable_prefix.index("完整解读报告") < context.stable_prefix.index("管理端配置")


@pytest.mark.asyncio
async def test_load_rejects_partial_tender_parse(client):
    from app import db as database
    from app.models import DiagnosisTask, WorkspaceFile
    from app.services.checklist_context import load_task_source

    async with database.SessionLocal() as session:
        task = DiagnosisTask(
            id="T-PARTIAL",
            tender_filename="t.docx",
            tender_path="/tmp/t.docx",
            bid_filename="b.docx",
            bid_path="/tmp/b.docx",
            tender_file_id="tender-file",
            status="generating_checklist",
            interpret_md_path="/tmp/interpret.md",
            config_snapshot=json.dumps([]),
        )
        session.add(task)
        session.add(
            WorkspaceFile(
                id="tender-file",
                task_id=task.id,
                label="招标文件",
                original_filename="t.docx",
                stored_path="/tmp/t.docx",
                kind="document",
                ext=".docx",
                parse_status="partial",
            )
        )
        await session.commit()

    with pytest.raises(ChecklistInputError, match="tender_parse_partial"):
        await load_task_source("T-PARTIAL")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_context.py -v
```

Expected: FAIL，提示 `checklist_context` 不存在。

- [ ] **Step 3: 增加配置和上下文实现**

在 `backend/app/config.py` 增加：

```python
CHECKLIST_AGENT = "mock"
CHECKLIST_AGENT_VERSION = "1"
CHECKLIST_SCHEMA_VERSION = "1"
CHECKLIST_SINGLE_PASS_TOKENS = 24_000
CHECKLIST_CHUNK_TOKENS = 12_000
CHECKLIST_CHUNK_OVERLAP_TOKENS = 500
CHECKLIST_MAX_ITEMS_PER_CATEGORY = 20
CHECKLIST_PARSE_POLL_SECONDS = 0.1
```

创建 `backend/app/services/checklist_context.py`，定义稳定数据结构和实现：

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import db as database
from app.models import DiagnosisTask, WorkspaceFile


class ChecklistInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class PromptCall:
    stable_prefix: str
    tender_segment: str


@dataclass(frozen=True)
class PromptContext:
    stable_prefix: str
    segments: list[str]
    calls: list[PromptCall]


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _tail_by_tokens(text: str, tokens: int) -> str:
    return text[-tokens * 4 :] if tokens > 0 else ""


def split_tender_markdown(
    markdown: str,
    *,
    threshold_tokens: int,
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    if estimate_tokens(markdown) <= threshold_tokens:
        return [markdown]
    sections = re.split(r"(?=^#{1,6}\s)", markdown, flags=re.MULTILINE)
    sections = [section for section in sections if section]
    limit = chunk_tokens * 4
    segments: list[str] = []
    current = ""
    for section in sections:
        parts = [section[i : i + limit] for i in range(0, len(section), limit)] or [section]
        for part in parts:
            if current and len(current) + len(part) > limit:
                segments.append(current)
                current = _tail_by_tokens(current, overlap_tokens) + part
            else:
                current += part
    if current:
        segments.append(current)
    return segments


def build_prompt_context(
    *,
    tender_markdown: str,
    interpret_markdown: str,
    admin_configs: list[dict[str, Any]],
    threshold_tokens: int,
    chunk_tokens: int,
    overlap_tokens: int,
) -> PromptContext:
    stable_prefix = (
        "## 固定生成规则\n"
        "逐项输出可追溯、可检索、可独立判断的检查项，遵循 schema_version=1。\n"
        "## 完整解读报告\n"
        f"{interpret_markdown}\n"
        "## 管理端配置（软参考）\n"
        f"{json.dumps(admin_configs, ensure_ascii=False, sort_keys=True)}\n"
        "## 当前招标正文分片\n"
    )
    segments = split_tender_markdown(
        tender_markdown,
        threshold_tokens=threshold_tokens,
        chunk_tokens=chunk_tokens,
        overlap_tokens=overlap_tokens,
    )
    return PromptContext(
        stable_prefix=stable_prefix,
        segments=segments,
        calls=[PromptCall(stable_prefix, segment) for segment in segments],
    )


async def load_task_source(task_id: str) -> tuple[DiagnosisTask, str, str, list[dict[str, Any]]]:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise ChecklistInputError("task_not_found")
        if not task.tender_file_id:
            raise ChecklistInputError("tender_file_missing")
        tender = await session.get(WorkspaceFile, task.tender_file_id)
        if tender is None:
            raise ChecklistInputError("tender_file_missing")
        if tender.parse_status != "succeeded":
            raise ChecklistInputError(f"tender_parse_{tender.parse_status}")
        if not tender.md_path or not Path(tender.md_path).is_file():
            raise ChecklistInputError("tender_markdown_missing")
        if not task.interpret_md_path or not Path(task.interpret_md_path).is_file():
            raise ChecklistInputError("interpret_markdown_missing")
        return (
            task,
            Path(tender.md_path).read_text(encoding="utf-8"),
            Path(task.interpret_md_path).read_text(encoding="utf-8"),
            json.loads(task.config_snapshot or "[]"),
        )
```

- [ ] **Step 4: 运行测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_context.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/config.py backend/app/services/checklist_context.py backend/tests/test_checklist_context.py
git commit -m "feat: build cache-friendly checklist context"
```

---

### Task 3: ChecklistAgent 协议与确定性 Mock

**Files:**
- Modify: `backend/app/engine/base.py`
- Create: `backend/app/engine/checklist_mock.py`
- Create: `backend/tests/test_checklist_agent.py`

- [ ] **Step 1: 写 Agent 输出与分片前缀测试**

创建 `backend/tests/test_checklist_agent.py`：

```python
import pytest

from app.engine.checklist_mock import MockChecklistAgent
from app.services.checklist_context import build_prompt_context


@pytest.mark.asyncio
async def test_mock_checklist_agent_returns_complete_dynamic_groups():
    context = build_prompt_context(
        tender_markdown="# 资格要求\n营业执照须有效\n# 评分办法\n业绩得分",
        interpret_markdown="# 解读\n注意资格和评分",
        admin_configs=[{"id": 7, "title": "证照检查", "importance": "high"}],
        threshold_tokens=2,
        chunk_tokens=5,
        overlap_tokens=1,
    )
    agent = MockChecklistAgent()
    draft = await agent.generate(task_id="T-1", context=context)
    assert draft.schema_version == "1"
    assert draft.categories
    assert draft.items
    assert all(item.category_id in {category.id for category in draft.categories} for item in draft.items)
    assert all(item.source_references for item in draft.items)
    assert all(item.requirement and item.technique for item in draft.items)
    assert agent.prompt_prefixes
    assert len(set(agent.prompt_prefixes)) == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_agent.py -v
```

Expected: FAIL，提示协议或 Mock Agent 不存在。

- [ ] **Step 3: 定义协议数据结构**

在 `backend/app/engine/base.py` 增加：

```python
@dataclass(frozen=True)
class ChecklistCategoryDraft:
    id: str
    name: str
    description: str
    retrieval_query: str
    expected_locations: list[str]
    sort_order: int


@dataclass(frozen=True)
class ChecklistItemDraft:
    id: str
    category_id: str
    title: str
    requirement: str
    technique: str
    importance: str
    source_references: list[dict[str, Any]]
    retrieval_hints: list[str]
    expected_evidence: list[str]
    compliance_rules: dict[str, str]
    consequence_rules: dict[str, str]
    admin_config_refs: list[int]
    sort_order: int


@dataclass(frozen=True)
class ChecklistDraft:
    schema_version: str
    categories: list[ChecklistCategoryDraft]
    items: list[ChecklistItemDraft]
    raw_response: dict[str, Any]


class ChecklistAgent(Protocol):
    async def generate(
        self, *, task_id: str, context: "PromptContext"
    ) -> ChecklistDraft: ...
```

通过 `TYPE_CHECKING` 导入 `PromptContext`，避免运行时循环依赖。

- [ ] **Step 4: 实现确定性 Mock Agent**

创建 `backend/app/engine/checklist_mock.py`。实现必须：

1. 遍历 `context.calls` 并记录每次 `stable_prefix`。
2. 从标题关键词把候选项归入动态位置分类，例如“资格/证照”归入“资格证明材料”，“评分/业绩”归入“商务评分材料”，其余归入“综合响应材料”。
3. 对同标题候选项按规范化标题去重。
4. 为每项填充非空来源引用、检索提示、预期证据、符合性规则和后果规则。
5. 管理配置只作为候选提示；正文仍至少产生一条检查项。

核心接口：

```python
class MockChecklistAgent:
    agent_type = "mock"
    agent_version = "1"

    def __init__(self) -> None:
        self.prompt_prefixes: list[str] = []

    async def generate(self, *, task_id: str, context: PromptContext) -> ChecklistDraft:
        self.prompt_prefixes = [call.stable_prefix for call in context.calls]
        candidates = [
            self._candidate_from_segment(task_id, index, call.tender_segment)
            for index, call in enumerate(context.calls)
        ]
        return self._merge_and_group(candidates)
```

`_candidate_from_segment` 与 `_merge_and_group` 返回 `base.py` 中定义的完整 dataclass，不返回松散字典。

- [ ] **Step 5: 运行测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_agent.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/engine/base.py backend/app/engine/checklist_mock.py backend/tests/test_checklist_agent.py
git commit -m "feat: add checklist agent protocol and mock"
```

---

### Task 4: 清单校验、原子发布与 Artifact

**Files:**
- Modify: `backend/app/services/artifact.py`
- Create: `backend/app/services/checklist_service.py`
- Create: `backend/tests/test_checklist_service.py`

- [ ] **Step 1: 写校验失败不发布和成功发布测试**

创建 `backend/tests/test_checklist_service.py`，使用真实临时数据库构造任务、成功解析的 `WorkspaceFile` 和解读 Markdown。覆盖：

```python
import json
import uuid

import pytest
from sqlalchemy import select

from app import db as database
from app.engine.base import ChecklistDraft, ChecklistItemDraft
from app.engine.checklist_mock import MockChecklistAgent
from app.models import ChecklistCategory, ChecklistItem, DiagnosisTask, WorkspaceFile
from app.services.checklist_service import ChecklistService, ChecklistValidationError


async def seed_ready_checklist_task(tmp_path) -> str:
    task_id = f"T-{uuid.uuid4().hex[:8]}"
    tender_md = tmp_path / f"{task_id}-tender.md"
    interpret_md = tmp_path / f"{task_id}-interpret.md"
    tender_md.write_text("# 资格要求\n营业执照须有效", encoding="utf-8")
    interpret_md.write_text("# 解读报告\n注意资格要求", encoding="utf-8")
    async with database.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="t.docx",
                tender_path="/tmp/t.docx",
                bid_filename="b.docx",
                bid_path="/tmp/b.docx",
                tender_file_id=f"{task_id}-tender",
                status="generating_checklist",
                interpret_md_path=str(interpret_md),
                config_snapshot=json.dumps(
                    [{"id": 1, "title": "证照检查", "importance": "high"}],
                    ensure_ascii=False,
                ),
            )
        )
        session.add(
            WorkspaceFile(
                id=f"{task_id}-tender",
                task_id=task_id,
                label="招标文件",
                original_filename="t.docx",
                stored_path="/tmp/t.docx",
                kind="document",
                ext=".docx",
                parse_status="succeeded",
                md_path=str(tender_md),
            )
        )
        await session.commit()
    return task_id


class InvalidChecklistAgent:
    agent_type = "mock"
    agent_version = "invalid"

    async def generate(self, *, task_id, context):
        valid = await MockChecklistAgent().generate(task_id=task_id, context=context)
        broken = valid.items[0]
        invalid_item = ChecklistItemDraft(
            **{**broken.__dict__, "source_references": []}
        )
        return ChecklistDraft(
            schema_version=valid.schema_version,
            categories=valid.categories,
            items=[invalid_item, *valid.items[1:]],
            raw_response={"invalid": True},
        )


@pytest.mark.asyncio
async def test_publish_checklist_is_atomic(client, monkeypatch, tmp_path):
    task_id = await seed_ready_checklist_task(tmp_path)
    service = ChecklistService(agent=MockChecklistAgent())
    generation_id = await service.generate_for_task(task_id)

    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        categories = (
            await session.execute(
                select(ChecklistCategory).where(ChecklistCategory.generation_id == generation_id)
            )
        ).scalars().all()
        items = (
            await session.execute(
                select(ChecklistItem).where(ChecklistItem.generation_id == generation_id)
            )
        ).scalars().all()
        assert task.current_checklist_generation_id == generation_id
        assert categories and items
        assert task.progress_total == len(items)


@pytest.mark.asyncio
async def test_invalid_agent_output_keeps_current_generation_unset(client, tmp_path):
    task_id = await seed_ready_checklist_task(tmp_path)
    service = ChecklistService(agent=InvalidChecklistAgent())
    with pytest.raises(ChecklistValidationError, match="source_references"):
        await service.generate_for_task(task_id)
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        assert task.current_checklist_generation_id is None
        assert task.status == "failed"
        assert "source_references" in (task.error_message or "")
```

测试辅助 `InvalidChecklistAgent` 返回一条 `source_references=[]` 的检查项；`seed_ready_checklist_task` 写入真实临时 Markdown 与解读文件。

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_service.py -v
```

Expected: FAIL，提示 `ChecklistService` 不存在。

- [ ] **Step 3: 增加 Artifact 写入函数**

在 `backend/app/services/artifact.py` 增加：

```python
def write_checklist_json(task_id: str, filename: str, payload: dict[str, Any]) -> Path:
    dest = ensure_artifact_dirs(task_id) / "json" / _safe_name(filename)
    dest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return dest
```

并在文件顶部导入 `json`。

- [ ] **Step 4: 实现严格校验和发布服务**

创建 `backend/app/services/checklist_service.py`，定义：

```python
class ChecklistValidationError(RuntimeError):
    pass


ALLOWED_IMPORTANCE = {"high", "medium", "low"}
ALLOWED_COMPLIANCE = {
    "satisfied",
    "violated",
    "cannot_satisfy",
    "insufficient_evidence",
}
ALLOWED_CONSEQUENCES = {
    "no_score",
    "bid_unusable",
    "score_risk",
    "general_risk",
}
```

`validate_draft(draft, tender_length, max_items_per_category)` 必须检查：

- Schema 为 `"1"`。
- 分类和检查项 ID 全局唯一。
- 分类名称、说明、查询非空。
- 每项只引用已存在分类。
- 标题、要求、技巧非空，规范化标题不重复。
- `importance` 合法。
- `source_references` 非空，每个引用的 `0 <= start < end <= tender_length`。
- 符合性规则键属于允许集合；后果规则键属于允许集合。
- 分类检查项数量不超过上限。

`ChecklistService.generate_for_task` 的顺序固定：

```python
async def generate_for_task(self, task_id: str) -> int:
    task, tender_md, interpret_md, configs = await load_task_source(task_id)
    context = build_prompt_context(
        tender_markdown=tender_md,
        interpret_markdown=interpret_md,
        admin_configs=configs,
        threshold_tokens=CHECKLIST_SINGLE_PASS_TOKENS,
        chunk_tokens=CHECKLIST_CHUNK_TOKENS,
        overlap_tokens=CHECKLIST_CHUNK_OVERLAP_TOKENS,
    )
    input_hash = hashlib.sha256(
        (context.stable_prefix + "\n".join(context.segments)).encode("utf-8")
    ).hexdigest()
    generation_id = await self._create_attempt(task_id, input_hash, configs)
    try:
        draft = await self.agent.generate(task_id=task_id, context=context)
        validate_draft(draft, len(tender_md), CHECKLIST_MAX_ITEMS_PER_CATEGORY)
        raw_path = artifact.write_checklist_json(
            task_id, f"checklist-generation-{generation_id}-raw.json", asdict(draft)
        )
        await self._publish(task_id, generation_id, draft, str(raw_path))
        return generation_id
    except Exception as exc:
        await self._fail(task_id, generation_id, str(exc))
        raise
```

`_publish` 先把 Agent 局部 ID 映射为数据库全局 ID，避免不同任务的 `cat-1` / `item-1` 冲突：

```python
def _db_id(prefix: str, generation_id: int, local_id: str) -> str:
    digest = hashlib.sha1(local_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{generation_id}-{digest}"
```

先建立全部 `category_id_map`，再写分类和使用映射后 `category_id` 的检查项。`_publish` 在一个数据库事务内插入分类和检查项、将生成记录改为 `succeeded`、写 `finished_at`、设置 `task.current_checklist_generation_id`、`progress_done=0`、`progress_total=len(items)`。提交成功后再写 `checklist.json` 正式 Artifact。

- [ ] **Step 5: 运行服务测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_service.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/artifact.py backend/app/services/checklist_service.py backend/tests/test_checklist_service.py
git commit -m "feat: validate and publish generated checklists"
```

---

### Task 5: 检查项查询 Schema 与 API

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/tasks.py`
- Create: `backend/tests/test_checklist_api.py`

- [ ] **Step 1: 写清单查询与重试冲突测试**

创建 `backend/tests/test_checklist_api.py`：

```python
import json
import uuid

import pytest
import pytest_asyncio

from app import db as database
from app.engine.checklist_mock import MockChecklistAgent
from app.models import DiagnosisTask, WorkspaceFile
from app.services.checklist_service import ChecklistService


async def _seed_api_task(tmp_path, parse_status: str) -> str:
    task_id = f"T-{uuid.uuid4().hex[:8]}"
    tender_md = tmp_path / f"{task_id}-tender.md"
    interpret_md = tmp_path / f"{task_id}-interpret.md"
    tender_md.write_text("# 资格要求\n营业执照须有效", encoding="utf-8")
    interpret_md.write_text("# 解读报告\n注意资格要求", encoding="utf-8")
    async with database.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="t.docx",
                tender_path="/tmp/t.docx",
                bid_filename="b.docx",
                bid_path="/tmp/b.docx",
                tender_file_id=f"{task_id}-tender",
                status="generating_checklist" if parse_status == "succeeded" else "failed",
                interpret_md_path=str(interpret_md),
                config_snapshot="[]",
                error_message=None if parse_status == "succeeded" else "tender_parse_failed",
            )
        )
        session.add(
            WorkspaceFile(
                id=f"{task_id}-tender",
                task_id=task_id,
                label="招标文件",
                original_filename="t.docx",
                stored_path="/tmp/t.docx",
                kind="document",
                ext=".docx",
                parse_status=parse_status,
                md_path=str(tender_md) if parse_status == "succeeded" else None,
            )
        )
        await session.commit()
    return task_id


@pytest_asyncio.fixture
async def ready_checklist_task(client, tmp_path):
    task_id = await _seed_api_task(tmp_path, "succeeded")
    await ChecklistService(agent=MockChecklistAgent()).generate_for_task(task_id)
    return task_id


@pytest_asyncio.fixture
async def failed_parse_task(client, tmp_path):
    return await _seed_api_task(tmp_path, "failed")


@pytest.mark.asyncio
async def test_get_checklist_returns_nested_categories(client, ready_checklist_task):
    task_id = ready_checklist_task
    response = await client.get(f"/api/tasks/{task_id}/checklist")
    assert response.status_code == 200
    body = response.json()
    assert body["generation"]["status"] == "succeeded"
    assert body["summary"]["item_count"] > 0
    assert body["summary"]["category_count"] == len(body["categories"])
    assert body["categories"][0]["items"][0]["source_references"]


@pytest.mark.asyncio
async def test_retry_requires_successful_tender_parse(client, failed_parse_task):
    response = await client.post(f"/api/tasks/{failed_parse_task}/checklist/retry")
    assert response.status_code == 409
    assert "tender_parse_failed" in response.text
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_api.py -v
```

Expected: FAIL，接口返回 404。

- [ ] **Step 3: 增加 Pydantic Schema**

在 `backend/app/schemas.py` 增加：

```python
class ChecklistItemOut(BaseModel):
    id: str
    title: str
    requirement: str
    technique: str
    importance: str
    source_references: list[dict]
    retrieval_hints: list[str]
    expected_evidence: list[str]
    compliance_rules: dict[str, str]
    consequence_rules: dict[str, str]
    admin_config_refs: list[int]
    sort_order: int


class ChecklistCategoryOut(BaseModel):
    id: str
    name: str
    description: str
    retrieval_query: str
    expected_locations: list[str]
    sort_order: int
    items: list[ChecklistItemOut]


class ChecklistGenerationOut(BaseModel):
    id: int
    status: str
    agent_type: str
    agent_version: str
    schema_version: str
    error_message: Optional[str]
    created_at: datetime
    finished_at: Optional[datetime]


class ChecklistSummaryOut(BaseModel):
    category_count: int
    item_count: int
    importance_counts: dict[str, int]


class ChecklistReportOut(BaseModel):
    generation: ChecklistGenerationOut
    summary: ChecklistSummaryOut
    categories: list[ChecklistCategoryOut]
```

在 `ResultOut` 增加：

```python
checklist_item_id: Optional[str]
compliance_status: Optional[str]
consequence_tags: List[str] = []
```

- [ ] **Step 4: 实现查询与重试路由**

在 `ChecklistService` 增加 `get_report(task_id)`，按 `sort_order` 查询当前 generation 的分类和检查项，并把 JSON 文本反序列化为 Schema 所需结构。

在 `backend/app/api/tasks.py` 增加：

```python
@router.get("/{task_id}/checklist", response_model=ChecklistReportOut)
async def get_checklist(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(DiagnosisTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.current_checklist_generation_id is None:
        raise HTTPException(404, "Checklist not available")
    return await checklist_service.get_report(task_id)


@router.post("/{task_id}/checklist/retry", status_code=202)
async def retry_checklist(task_id: str, db: AsyncSession = Depends(get_db)):
    try:
        await scheduler.retry_checklist(task_id)
    except LookupError:
        raise HTTPException(404, "Task not found")
    except SchedulerConflict as exc:
        raise HTTPException(409, str(exc))
    return {"task_id": task_id, "status": "generating_checklist"}
```

创建任务时把 `progress_total=len(snapshot)` 改为：

```python
progress_total=0,
```

`config_snapshot` 仍保存管理配置审计快照。

- [ ] **Step 5: 运行 API 测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_checklist_api.py tests/test_tasks.py -v
```

Expected: PASS；若 `test_tasks.py` 仍断言创建时 `progress_total` 为配置数，将其改为 `0`。

- [ ] **Step 6: 提交**

```bash
git add backend/app/schemas.py backend/app/api/tasks.py backend/app/services/checklist_service.py backend/tests/conftest.py backend/tests/test_checklist_api.py backend/tests/test_tasks.py
git commit -m "feat: expose task checklist API"
```

---

### Task 6: 分类批量诊断协议与服务

**Files:**
- Modify: `backend/app/engine/base.py`
- Create: `backend/app/engine/batch_mock.py`
- Create: `backend/app/services/diagnosis_service.py`
- Create: `backend/tests/test_batch_diagnosis.py`

- [ ] **Step 1: 写完整映射和整批失败测试**

创建 `backend/tests/test_batch_diagnosis.py`：

```python
import pytest

from app.engine.base import BatchDiagnosisItemResult
from app.engine.batch_mock import MockBatchDiagnosisEngine, MockRetrievalProvider
from app.services.diagnosis_service import BatchResultError, validate_batch_results


def make_result(item_id: str) -> BatchDiagnosisItemResult:
    return BatchDiagnosisItemResult(
        checklist_item_id=item_id,
        compliance_status="satisfied",
        consequence_tags=[],
        evidence="证据",
        explanation="说明",
        suggestion="建议",
    )


@pytest.mark.asyncio
async def test_mock_batch_returns_one_result_per_item():
    items = [
        {"id": "i-1", "title": "资质", "requirement": "须有效"},
        {"id": "i-2", "title": "业绩", "requirement": "须提供合同"},
    ]
    chunks = await MockRetrievalProvider().retrieve(
        task_id="T-1", category={"id": "c-1"}, items=items
    )
    results = await MockBatchDiagnosisEngine(delay_seconds=0).diagnose_category(
        task_id="T-1",
        category={"id": "c-1", "name": "资格材料"},
        items=items,
        retrieved_chunks=chunks,
    )
    validate_batch_results(items, results)
    assert {result.checklist_item_id for result in results} == {"i-1", "i-2"}
    assert all(result.compliance_status in {
        "satisfied", "violated", "cannot_satisfy", "insufficient_evidence"
    } for result in results)


def test_batch_validation_rejects_missing_item():
    items = [{"id": "i-1"}, {"id": "i-2"}]
    with pytest.raises(BatchResultError, match="missing"):
        validate_batch_results(items, [make_result("i-1")])
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_batch_diagnosis.py -v
```

Expected: FAIL，提示批量协议不存在。

- [ ] **Step 3: 定义批量协议**

在 `backend/app/engine/base.py` 增加：

```python
@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    content: str
    source: str


@dataclass(frozen=True)
class BatchDiagnosisItemResult:
    checklist_item_id: str
    compliance_status: str
    consequence_tags: list[str]
    evidence: str
    explanation: str
    suggestion: str


class RetrievalProvider(Protocol):
    async def retrieve(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[RetrievedChunk]: ...


class BatchDiagnosisEngine(Protocol):
    async def diagnose_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[BatchDiagnosisItemResult]: ...
```

- [ ] **Step 4: 实现 Mock 与批次持久化**

`backend/app/engine/batch_mock.py` 使用 `sha256(f"{task_id}:{item_id}")` 确定性选择符合性和后果标签；`MockRetrievalProvider` 返回一个注明分类名称的内容块。

`backend/app/services/diagnosis_service.py` 实现：

```python
def validate_batch_results(
    items: list[dict[str, Any]],
    results: list[BatchDiagnosisItemResult],
) -> None:
    expected = {item["id"] for item in items}
    actual = [result.checklist_item_id for result in results]
    if len(actual) != len(set(actual)):
        raise BatchResultError("duplicate checklist item result")
    missing = expected - set(actual)
    unknown = set(actual) - expected
    if missing:
        raise BatchResultError(f"missing checklist items: {sorted(missing)}")
    if unknown:
        raise BatchResultError(f"unknown checklist items: {sorted(unknown)}")
```

`diagnose_and_persist_category(task_id, category, items, sort_offset)` 必须先完成召回、调用和完整校验，再在单个事务中写入该分类全部 `DiagnosisResult`；`result` 保存中文显示值，`compliance_status` 保存机器枚举，`consequence_tags` 保存 JSON。

- [ ] **Step 5: 运行测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_batch_diagnosis.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add backend/app/engine/base.py backend/app/engine/batch_mock.py backend/app/services/diagnosis_service.py backend/tests/test_batch_diagnosis.py
git commit -m "feat: add category batch diagnosis"
```

---

### Task 7: 调度器接入生成阶段、解析等待与重试

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: 更新集成测试覆盖新状态流**

把 scheduler 测试任务输入改为可解析的最小 DOCX，并增加辅助：

```python
def _docx_bytes() -> bytes:
    from docx import Document
    from io import BytesIO

    buffer = BytesIO()
    doc = Document()
    doc.add_heading("资格要求", level=1)
    doc.add_paragraph("投标人须提供有效营业执照。")
    doc.add_heading("评分办法", level=1)
    doc.add_paragraph("类似业绩最高得 10 分。")
    doc.save(buffer)
    return buffer.getvalue()
```

修改 `test_scheduler_runs_to_completion`：

```python
status = await scheduler.wait_for_terminal(task_id, timeout=10)
assert status == "completed"
detail = (await client.get(f"/api/tasks/{task_id}")).json()
checklist = (await client.get(f"/api/tasks/{task_id}/checklist")).json()
item_count = checklist["summary"]["item_count"]
assert detail["progress_done"] == item_count
assert detail["progress_total"] == item_count
assert len(detail["results"]) == item_count
assert all(result["checklist_item_id"] for result in detail["results"])
```

增加/替换测试辅助：

```python
async def _create_valid_task(client: AsyncClient) -> dict:
    payload = _docx_bytes()
    files = {
        "tender_file": (
            "tender.docx",
            io.BytesIO(payload),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "bid_file": (
            "bid.docx",
            io.BytesIO(payload),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    response = await client.post(
        "/api/tasks",
        data={"background": "bg", "requirements": "req"},
        files=files,
    )
    assert response.status_code == 201
    return response.json()


async def _create_task_with_invalid_tender(client: AsyncClient) -> dict:
    files = {
        "tender_file": (
            "tender.docx",
            io.BytesIO(b"not-a-docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        "bid_file": (
            "bid.docx",
            io.BytesIO(_docx_bytes()),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    response = await client.post("/api/tasks", files=files)
    assert response.status_code == 201
    return response.json()


async def wait_for_status(client: AsyncClient, task_id: str, expected: str) -> dict:
    for _ in range(500):
        detail = (await client.get(f"/api/tasks/{task_id}")).json()
        if detail["status"] == expected:
            return detail
        if detail["status"] in {"completed", "failed", "stopped"}:
            raise AssertionError(f"task reached {detail['status']} before {expected}")
        await asyncio.sleep(0.01)
    raise AssertionError(f"task never reached {expected}")
```

增加：

```python
@pytest.mark.asyncio
async def test_parse_failure_blocks_checklist_without_fallback(client):
    body = await _create_task_with_invalid_tender(client)
    status = await scheduler.wait_for_terminal(body["id"], timeout=10)
    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{body['id']}")).json()
    assert "tender_parse_failed" in (detail["error_message"] or "")
    assert (await client.get(f"/api/tasks/{body['id']}/checklist")).status_code == 404


@pytest.mark.asyncio
async def test_cannot_pause_while_generating_checklist(client, monkeypatch):
    gate = asyncio.Event()
    original = ChecklistService.generate_for_task

    async def blocked(self, task_id):
        await gate.wait()
        return await original(self, task_id)

    monkeypatch.setattr(ChecklistService, "generate_for_task", blocked)
    body = await _create_valid_task(client)
    await wait_for_status(client, body["id"], "generating_checklist")
    assert (await client.post(f"/api/tasks/{body['id']}/pause")).status_code == 409
    gate.set()
    assert await scheduler.wait_for_terminal(body["id"], timeout=10) == "completed"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_scheduler.py -v
```

Expected: FAIL，因为调度器仍直接使用配置快照逐项诊断。

- [ ] **Step 3: 重构 `_run` 为阶段编排**

在 `backend/app/services/scheduler.py`：

- 把 `"generating_checklist"` 加入 `STOPPABLE_STATUSES`。
- 解读成功后设置 `status="generating_checklist"`。
- 新增等待招标解析终态的函数：

```python
class _StopRequested(Exception):
    pass


async def _wait_for_tender_parse(task_id: str) -> None:
    while True:
        if _should_stop(task_id):
            raise _StopRequested
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            tender = (
                await session.get(WorkspaceFile, task.tender_file_id)
                if task and task.tender_file_id
                else None
            )
            if tender is None:
                raise RuntimeError("tender_file_missing")
            if tender.parse_status == "succeeded":
                return
            if tender.parse_status in {"partial", "failed", "skipped"}:
                raise RuntimeError(f"tender_parse_{tender.parse_status}")
        await asyncio.sleep(CHECKLIST_PARSE_POLL_SECONDS)
```

- 调用 `ChecklistService(agent=MockChecklistAgent()).generate_for_task(task_id)`。
- 生成成功后在数据库中把任务状态改为 `diagnosing`，再加载并执行分类。
- 按当前 generation 查询分类和检查项。
- 每个分类调用 `diagnose_and_persist_category`，成功后按本分类项数更新 `progress_done`。
- pause/stop 检查点置于分类之间。
- 完成全部分类后沿用报告生成。

在 `_run` 的 `except asyncio.CancelledError` 之前增加：

```python
except _StopRequested:
    await _mark_stopped(task_id)
    return
```

`CancelledError` 只用于测试清理或进程取消；用户 stop 使用 `_StopRequested`，不得落入通用 failed 分支。

- [ ] **Step 4: 实现 `retry_checklist`**

在 `scheduler.py` 增加：

```python
async def retry_checklist(task_id: str) -> None:
    async with database.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise LookupError(task_id)
        if task.status != "failed" or not task.interpret_md_path:
            raise SchedulerConflict(f"cannot retry checklist in status {task.status}")
        tender = await session.get(WorkspaceFile, task.tender_file_id)
        if tender is None or tender.parse_status != "succeeded":
            status = tender.parse_status if tender else "missing"
            raise SchedulerConflict(f"tender_parse_{status}")
        if task.current_checklist_generation_id is not None:
            raise SchedulerConflict("checklist already published")
        task.status = "generating_checklist"
        task.error_message = None
        task.finished_at = None
        task.updated_at = utcnow()
        await session.commit()
    await start_task(task_id)
```

确保 `_run` 检测到已有 `interpret_md_path` 时跳过解读，直接进入检查项生成。

- [ ] **Step 5: 更新测试 fixture 延迟**

在 `backend/tests/conftest.py` 把检查项和批诊断延迟 monkeypatch 为 0，并保留解析调度器真实运行：

```python
monkeypatch.setattr("app.services.scheduler.CHECKLIST_PARSE_POLL_SECONDS", 0.01)
monkeypatch.setattr("app.services.scheduler.MOCK_ITEM_DELAY_SECONDS", 0.01)
```

- [ ] **Step 6: 运行调度和 API 集成测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_scheduler.py tests/test_checklist_api.py -v
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/scheduler.py backend/tests/conftest.py backend/tests/test_scheduler.py
git commit -m "feat: orchestrate checklist generation and batch diagnosis"
```

---

### Task 8: 结构化诊断结果与报告兼容

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/api/tasks.py`
- Modify: `backend/app/services/report.py`
- Modify: `backend/tests/test_report.py`

- [ ] **Step 1: 写报告两维结果测试**

在 `backend/tests/test_report.py` 增加：

```python
def test_build_markdown_contains_compliance_and_consequences():
    markdown = build_markdown(
        "T-1",
        [{
            "content_title": "营业执照",
            "description": "须在有效期内",
            "result": "违反",
            "compliance_status": "violated",
            "consequence_tags": ["bid_unusable", "no_score"],
            "evidence": "证件已过期",
            "suggestion": "更新证件",
        }],
    )
    assert "**符合性：** 违反" in markdown
    assert "**后果：** 标书不可用、无法得分" in markdown
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
../.venv/bin/python -m pytest tests/test_report.py -v
```

Expected: FAIL，报告尚未输出后果标签。

- [ ] **Step 3: 更新 Schema 序列化和报告**

在 `_task_to_out` 返回结果前，把 ORM 结果转换为含反序列化标签的 `ResultOut`：

```python
result_rows = [
    ResultOut(
        id=row.id,
        task_id=row.task_id,
        config_id=row.config_id,
        checklist_item_id=row.checklist_item_id,
        content_title=row.content_title,
        description=row.description,
        result=row.result,
        compliance_status=row.compliance_status,
        consequence_tags=json.loads(row.consequence_tags or "[]"),
        evidence=row.evidence,
        suggestion=row.suggestion,
        sort_order=row.sort_order,
        created_at=row.created_at,
    )
    for row in results
]
```

在 `report.py` 定义中文标签：

```python
COMPLIANCE_LABELS = {
    "satisfied": "满足",
    "violated": "违反",
    "cannot_satisfy": "不能满足",
    "insufficient_evidence": "证据不足",
}
CONSEQUENCE_LABELS = {
    "no_score": "无法得分",
    "bid_unusable": "标书不可用",
    "score_risk": "扣分风险",
    "general_risk": "一般风险",
}
```

每条明细增加：

```python
f"- **符合性：** {COMPLIANCE_LABELS.get(item.get('compliance_status'), item.get('result', ''))}",
f"- **后果：** {'、'.join(CONSEQUENCE_LABELS.get(tag, tag) for tag in item.get('consequence_tags', [])) or '无'}",
```

`generate_and_save_reports` 查询 ORM 行时反序列化 `consequence_tags`。

- [ ] **Step 4: 运行报告与任务详情测试**

Run:

```bash
../.venv/bin/python -m pytest tests/test_report.py tests/test_tasks.py -v
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas.py backend/app/api/tasks.py backend/app/services/report.py backend/tests/test_report.py
git commit -m "feat: report structured diagnosis outcomes"
```

---

### Task 9: 前端检查项报告组件

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/src/components/ChecklistReport.jsx`
- Create: `frontend/src/components/ChecklistReport.test.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 增加测试依赖**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application/frontend
npm install -D vitest jsdom @testing-library/react @testing-library/jest-dom
```

在 `frontend/package.json` 的 scripts 增加：

```json
"test": "vitest run"
```

- [ ] **Step 2: 写组件失败测试**

创建 `frontend/src/components/ChecklistReport.test.jsx`：

```jsx
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ChecklistReport from './ChecklistReport'

const report = {
  summary: { category_count: 1, item_count: 1, importance_counts: { high: 1 } },
  categories: [{
    id: 'cat-1',
    name: '资格证明材料',
    description: '资格类材料',
    items: [{
      id: 'item-1',
      title: '营业执照有效性',
      requirement: '须在有效期内',
      technique: '检索证照并核对主体',
      importance: 'high',
      source_references: [{ section: '资格要求', start: 0, end: 12 }],
      retrieval_hints: ['营业执照'],
      expected_evidence: ['证照扫描件'],
      compliance_rules: { satisfied: '有效且一致' },
      consequence_rules: { bid_unusable: '缺失或失效' },
    }],
  }],
}

describe('ChecklistReport', () => {
  it('shows summary, categories and expandable machine fields', () => {
    render(<ChecklistReport report={report} status="completed" />)
    expect(screen.getByText('共 1 项')).toBeInTheDocument()
    expect(screen.getByText('营业执照有效性')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }))
    expect(screen.getByText(/资格要求/)).toBeInTheDocument()
    expect(screen.getByText(/bid_unusable/)).toBeInTheDocument()
  })

  it('shows retry on generation failure', () => {
    const retry = vi.fn()
    render(<ChecklistReport status="failed" error="schema invalid" onRetry={retry} />)
    fireEvent.click(screen.getByRole('button', { name: '重试生成' }))
    expect(retry).toHaveBeenCalledOnce()
  })
})
```

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
npm test -- ChecklistReport.test.jsx
```

Expected: FAIL，组件不存在。

- [ ] **Step 4: 实现组件**

创建 `ChecklistReport.jsx`，props 固定为：

```jsx
export default function ChecklistReport({
  report = null,
  status = '',
  error = '',
  loading = false,
  onRetry,
  workspaceUrl = '',
}) {
  // loading/generating、parse failure、generation failure、empty、success 五种状态
}
```

成功态必须：

- 展示项目数、分类数和 high/medium/low 数量。
- 用按钮筛选“全部”和各动态分类。
- 表格展示标题、要求、技巧、重要性、分类。
- 每行用 `<details>` 或显式按钮展开来源、检索提示、预期证据、符合性规则和后果规则。
- 所有数组和对象通过安全格式化函数展示，不使用 `dangerouslySetInnerHTML`。

在 `App.css` 增加 `.checklist-summary`、`.checklist-category-filter`、`.checklist-table`、`.checklist-details` 和移动端横向滚动样式；复用现有 `.importance-*`。

- [ ] **Step 5: 运行组件测试、lint 和 build**

Run:

```bash
npm test -- ChecklistReport.test.jsx
npm run lint
npm run build
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/ChecklistReport.jsx frontend/src/components/ChecklistReport.test.jsx frontend/src/App.css
git commit -m "feat: add checklist report component"
```

---

### Task 10: 任务详情三 Tab 与状态联动

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/pages/TaskDetailPage.jsx`
- Create: `frontend/src/pages/TaskDetailPage.test.jsx`
- Modify: `frontend/src/components/TaskCard.jsx`
- Modify: `frontend/src/pages/admin/AdminTasksPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 写三 Tab 和检查项按需加载失败测试**

创建 `frontend/src/pages/TaskDetailPage.test.jsx`：

```jsx
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskDetailPage from './TaskDetailPage'
import { getTask, getTaskChecklist } from '../api'

vi.mock('../api', () => ({
  getTask: vi.fn(),
  getTaskChecklist: vi.fn(),
  retryTaskChecklist: vi.fn(),
  fileUrl: vi.fn(() => '#file'),
  interpretHtmlUrl: vi.fn(() => '#interpret'),
  reportDocxUrl: vi.fn(() => '#report'),
}))

const task = {
  id: 'T-1',
  status: 'completed',
  tender_filename: 'tender.docx',
  bid_filename: 'bid.docx',
  background: '',
  requirements: '',
  progress_done: 1,
  progress_total: 1,
  interpret_markdown: '# 解读',
  report_markdown: '# 诊断',
  results: [],
}

const checklist = {
  summary: { category_count: 1, item_count: 1, importance_counts: { high: 1 } },
  categories: [{
    id: 'cat-1',
    name: '资格材料',
    description: '资格材料',
    items: [{
      id: 'item-1',
      title: '营业执照',
      requirement: '须有效',
      technique: '核对有效期',
      importance: 'high',
      source_references: [{ section: '资格要求', start: 0, end: 4 }],
      retrieval_hints: ['营业执照'],
      expected_evidence: ['证照'],
      compliance_rules: { satisfied: '有效' },
      consequence_rules: { bid_unusable: '失效' },
    }],
  }],
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/tasks/T-1']}>
      <Routes>
        <Route path="/tasks/:id" element={<TaskDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('TaskDetailPage checklist tab', () => {
  beforeEach(() => {
    getTask.mockResolvedValue(task)
    getTaskChecklist.mockResolvedValue(checklist)
  })

  it('orders reports as interpretation, checklist, diagnosis', async () => {
    renderPage()
    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      '解读报告',
      '检查项报告',
      '诊断报告',
    ])
  })

  it('loads and shows checklist when its tab is selected', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('tab', { name: '检查项报告' }))
    await waitFor(() => expect(getTaskChecklist).toHaveBeenCalledWith('T-1'))
    expect(await screen.findByText('营业执照')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 运行页面测试确认失败**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application/frontend
npm test -- TaskDetailPage.test.jsx
```

Expected: FAIL，因为 API 和第三个 Tab 尚不存在。

- [ ] **Step 3: 增加 API 客户端**

在 `frontend/src/api.js` 增加：

```javascript
export function getTaskChecklist(id) {
  return request(`/api/tasks/${id}/checklist`)
}

export function retryTaskChecklist(id) {
  return request(`/api/tasks/${id}/checklist/retry`, { method: 'POST' })
}
```

- [ ] **Step 4: 修改任务详情状态和按需加载**

在 `TaskDetailPage.jsx`：

```javascript
const STATUS_LABELS = {
  interpreting: '解读中',
  generating_checklist: '生成检查项中',
  diagnosing: '诊断中',
  running: '诊断中',
  paused: '已暂停',
  completed: '已完成',
  stopped: '已停止',
  failed: '失败',
}

const POLL_STATUSES = new Set([
  'interpreting',
  'generating_checklist',
  'diagnosing',
  'running',
  'paused',
])
```

增加状态：

```javascript
const [checklist, setChecklist] = useState(null)
const [checklistLoading, setChecklistLoading] = useState(false)
const [checklistError, setChecklistError] = useState('')
```

当 `reportTab === 'checklist'` 或任务状态进入 `diagnosing/paused/completed` 时调用 `getTaskChecklist(id)`；404 在 `interpreting/generating_checklist` 期间视为尚未生成，不显示通用网络错误。

重试函数：

```javascript
async function retryChecklist() {
  setChecklistLoading(true)
  try {
    await retryTaskChecklist(id)
    setChecklistError('')
    await load(true)
  } catch (err) {
    setChecklistError(err.message || '重试失败')
  } finally {
    setChecklistLoading(false)
  }
}
```

- [ ] **Step 5: 把报告区改为三个 Tab**

Tab 顺序必须为：

```jsx
<button onClick={() => setReportTab('interpret')}>解读报告</button>
<button onClick={() => setReportTab('checklist')}>检查项报告</button>
<button onClick={() => setReportTab('diagnosis')}>诊断报告</button>
```

检查项内容：

```jsx
<ChecklistReport
  report={checklist}
  status={status}
  error={checklistError || task.error_message || ''}
  loading={checklistLoading}
  onRetry={retryChecklist}
  workspaceUrl={`/workspaces/${task.id}`}
/>
```

解读和诊断原有分支保持行为不变；`generating_checklist` 时诊断 Tab 显示“检查项生成完成后开始诊断”。

- [ ] **Step 6: 更新列表和管理端状态**

在 `TaskCard.jsx`、`AdminTasksPage.jsx` 的 `STATUS_LABELS` 增加：

```javascript
generating_checklist: '生成检查项中',
```

管理端 `generating_checklist` 仅显示“停止”按钮，不显示暂停；逻辑与 `interpreting` 的停止按钮一致。

在 `App.css` 增加：

```css
.status-generating_checklist {
  background: #f5f3ff;
  color: #6941c6;
}
```

- [ ] **Step 7: 运行页面测试和前端验证**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application/frontend
npm test -- TaskDetailPage.test.jsx
npm test
npm run lint
npm run build
```

Expected: 全部 PASS，Vite 生成 `dist/`。

- [ ] **Step 8: 提交**

```bash
git add frontend/src/api.js frontend/src/pages/TaskDetailPage.jsx frontend/src/pages/TaskDetailPage.test.jsx frontend/src/components/TaskCard.jsx frontend/src/pages/admin/AdminTasksPage.jsx frontend/src/App.css
git commit -m "feat: show checklist report between task reports"
```

---

### Task 11: 全量回归、文档与验收

**Files:**
- Modify: `README.md`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: 增加冻结和管理配置快照回归测试**

在 `backend/tests/test_scheduler.py` 增加：

```python
@pytest.mark.asyncio
async def test_published_checklist_is_frozen_after_admin_config_change(client):
    await _seed_configs(client, 1)
    body = await _create_valid_task(client)
    assert await scheduler.wait_for_terminal(body["id"], timeout=10) == "completed"
    before = (await client.get(f"/api/tasks/{body['id']}/checklist")).json()

    configs = (await client.get("/api/configs")).json()
    await client.put(
        f"/api/configs/{configs[0]['id']}",
        json={
            "title": "已修改配置",
            "technique": "修改后技巧",
            "content_mode": "description",
            "content_text": "修改后内容",
            "importance": "low",
        },
    )

    after = (await client.get(f"/api/tasks/{body['id']}/checklist")).json()
    assert after == before
    assert (await client.post(f"/api/tasks/{body['id']}/checklist/retry")).status_code == 409
```

- [ ] **Step 2: 更新 README**

在 README 流程说明中写明：

```text
任务流水线：解读招标文件 → 生成检查项 → 按动态分类 Mock 诊断 → 生成诊断报告。
任务详情报告顺序：解读报告 → 检查项报告 → 诊断报告。
检查项生成必须等待招标文件工作区解析成功；解析失败时先在工作区重新解析，再重试检查项生成。
```

在验收清单增加检查项字段、动态分类、失败重试和管理配置快照冻结场景。

- [ ] **Step 3: 运行全量后端测试**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application/backend
../.venv/bin/python -m pytest
```

Expected: 全部 PASS。

- [ ] **Step 4: 运行全量前端验证**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application/frontend
npm test
npm run lint
npm run build
```

Expected: 全部 PASS。

- [ ] **Step 5: 一键启动手动验收**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application
.venv/bin/python startup.py --no-browser
```

验证：

1. 创建有效 DOCX/PDF 任务。
2. 状态经过解读、检查项生成、诊断和完成。
3. 三个报告 Tab 顺序正确。
4. 检查项主字段和展开机器字段完整。
5. 诊断结果数量等于检查项数量。
6. 终端按 `Ctrl+C`，前后端均正常退出。

- [ ] **Step 6: 提交**

```bash
git add README.md backend/tests/test_scheduler.py
git commit -m "docs: document generated checklist workflow"
```

---

## Spec Coverage Self-Review

- 任务级生成记录、动态分类、检查项模型：Task 1。
- 招标正文、完整解读报告、管理配置软参考：Tasks 2–4。
- 短文档单次、长文档章节/token 分片、稳定前缀缓存：Tasks 2–3。
- 严格 Schema、来源、分类、唯一性和规模校验：Task 4。
- 原始响应、正式 Artifact、原子发布：Task 4。
- 清单查询、失败状态与重试：Tasks 5、7。
- 分类级 Mock 召回和一次批诊断：Tasks 6–7。
- 两维诊断结果与报告：Tasks 1、6、8。
- 三 Tab 与明细展开：Tasks 9–10。
- 解析失败不降级、清单冻结、旧配置不影响历史任务：Tasks 7、11。
- SQLite 迁移、旧任务兼容和重启恢复：Task 1。

## Placeholder / Consistency Self-Review

- 状态统一使用 `interpreting | generating_checklist | diagnosing | paused | completed | failed | stopped`，只读兼容 `running`。
- 当前清单通过 `DiagnosisTask.current_checklist_generation_id` 指向成功版本。
- `ChecklistItem.id` 与 `DiagnosisResult.checklist_item_id` 均为 `String(64)`。
- JSON 数据库字段在 ORM 中使用 `Text`，API 边界统一反序列化为列表或对象。
- 符合性统一使用 `satisfied | violated | cannot_satisfy | insufficient_evidence`。
- 后果标签统一使用 `no_score | bid_unusable | score_risk | general_risk`。
- 本期不实现真实 HTTP 智能体、真实工作区召回或检查项下载。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-17-tender-checklist-generation.md`.

Two execution options:

1. **Subagent-Driven（推荐）** — 每个 Task 派发独立子代理，任务间进行规格与质量复核。
2. **Inline Execution** — 在当前会话按批次执行，并在阶段间设置检查点。
