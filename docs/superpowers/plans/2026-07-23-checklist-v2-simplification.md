# Checklist Schema v2 简化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 检查项生成切换到 schema v2——固定六类分类、Agent 只输出 items、判定/出处字段 Markdown 化，并端到端适配后端、批诊断与前端。

**Architecture:** 后端 `FIXED_CATEGORIES` 注入分类；`parse_checklist_payload` 解析 v2 items-only JSON；`merge_checklist_drafts` 按 `category_id` 分桶；DB 列名复用、语义改为 Markdown 字符串；API 按 `generation.schema_version` 分支，v1 只读时格式化为 Markdown 供前端统一渲染。

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / pytest；React (ChecklistReport.jsx)；Agent OS config JSON

**Spec:** `docs/superpowers/specs/2026-07-23-checklist-v2-simplification-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `backend/app/config.py` | `CHECKLIST_SCHEMA_VERSION = "2"` |
| `backend/app/services/checklist_context.py` | `FIXED_CATEGORIES` 常量 |
| `backend/app/engine/base.py` | `ChecklistItemDraft` v2 字段类型 |
| `backend/app/engine/checklist_agent_os.py` | v2 parser + 注入固定 categories |
| `backend/app/engine/checklist_merge.py` | 按 `category_id` 合并 |
| `backend/app/services/checklist_consequence.py` | **新建** Markdown 首行标签解析 |
| `backend/app/services/checklist_service.py` | validate/publish/load API v2 |
| `backend/app/services/scheduler.py` | offline consequence_tags 解析 |
| `backend/app/services/batch_diagnosis_context.py` | 批诊断提示词 Markdown 适配 |
| `backend/app/schemas.py` | `ChecklistItemOut` 字段改为 str |
| `frontend/src/components/ChecklistReport.jsx` | Markdown 文本展示 |
| `backend/tests/test_checklist_*.py` | 全面更新 fixture |
| `backend/tests/fake_checklist_invoke.py` | v2 fake payload |
| `docs/agents_config/tender_checklist_generator.json` | outputSchema v2 |

---

### Task 1: 固定分类常量 + schema version

**Files:**
- Create: `backend/app/services/checklist_categories.py`
- Modify: `backend/app/config.py`
- Test: `backend/tests/test_checklist_categories.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_checklist_categories.py`:

```python
from app.services.checklist_categories import (
    FIXED_CATEGORY_IDS,
    fixed_categories_draft,
)


def test_fixed_categories_has_six_entries_in_order():
    drafts = fixed_categories_draft()
    assert len(drafts) == 6
    assert [c.id for c in drafts] == [
        "cat_001",
        "cat_002",
        "cat_003",
        "cat_004",
        "cat_005",
        "cat_006",
    ]
    assert drafts[0].name == "废标红线"
    assert drafts[0].sort_order == 1


def test_fixed_category_ids_is_frozenset():
    assert "cat_001" in FIXED_CATEGORY_IDS
    assert "cat_999" not in FIXED_CATEGORY_IDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_categories.py -v`

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement**

Create `backend/app/services/checklist_categories.py`:

```python
from __future__ import annotations

from app.engine.base import ChecklistCategoryDraft

_FIXED = (
    ("cat_001", "废标红线", "导致否决/不予受理的重大偏差", "废标 否决 重大偏差 无效投标"),
    ("cat_002", "资质文件", "资格证明文件与合规材料", "资质 资格 营业执照 业绩 财务"),
    ("cat_003", "格式要求", "编制、签署、封装等形式要求", "格式 签字 盖章 密封 目录"),
    ("cat_004", "得分检查", "影响评分的响应与填报项", "得分 评分 折扣率 报价"),
    ("cat_005", "风险检查", "履约/一致性与潜在争议点", "风险 履约 一致性"),
    ("cat_006", "其他检查", "未归入上述类别的必要检查项", "其他 补充"),
)

FIXED_CATEGORY_IDS = frozenset(row[0] for row in _FIXED)


def fixed_categories_draft() -> list[ChecklistCategoryDraft]:
    return [
        ChecklistCategoryDraft(
            id=cat_id,
            name=name,
            description=description,
            retrieval_query=retrieval_query,
            expected_locations=[],
            sort_order=index,
        )
        for index, (cat_id, name, description, retrieval_query) in enumerate(
            _FIXED, start=1
        )
    ]
```

Modify `backend/app/config.py`:

```python
CHECKLIST_SCHEMA_VERSION = "2"
```

- [ ] **Step 4: Run test**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_categories.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/checklist_categories.py backend/app/config.py backend/tests/test_checklist_categories.py
git commit -m "feat: add fixed checklist categories and bump schema version to 2"
```

---

### Task 2: ChecklistItemDraft v2 类型 + consequence 解析工具

**Files:**
- Create: `backend/app/services/checklist_consequence.py`
- Modify: `backend/app/engine/base.py`
- Test: `backend/tests/test_checklist_consequence.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_checklist_consequence.py`:

```python
from app.services.checklist_consequence import parse_consequence_tags_from_markdown


def test_parse_tags_from_first_line():
    text = "[bid_unusable]\n未签字将被否决。"
    assert parse_consequence_tags_from_markdown(text) == ["bid_unusable"]


def test_parse_multiple_tags():
    text = "[score_risk, general_risk]\n扣分风险。"
    assert parse_consequence_tags_from_markdown(text) == ["score_risk", "general_risk"]


def test_parse_missing_returns_empty():
    assert parse_consequence_tags_from_markdown("无标签说明") == []
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_consequence.py -v`

- [ ] **Step 3: Implement parser**

Create `backend/app/services/checklist_consequence.py`:

```python
from __future__ import annotations

import re

_VALID_TAGS = frozenset({"bid_unusable", "score_risk", "no_score", "general_risk"})
_TAG_LINE = re.compile(r"^\[([^\]]+)\]")


def parse_consequence_tags_from_markdown(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    first_line = text.strip().splitlines()[0].strip()
    match = _TAG_LINE.match(first_line)
    if not match:
        return []
    raw = match.group(1)
    tags: list[str] = []
    for part in raw.split(","):
        tag = part.strip()
        if tag in _VALID_TAGS and tag not in tags:
            tags.append(tag)
    return tags
```

Modify `backend/app/engine/base.py` — `ChecklistItemDraft` fields:

```python
@dataclass(frozen=True)
class ChecklistItemDraft:
    id: str
    category_id: str
    title: str
    requirement: str
    technique: str
    importance: str
    source_citations: str
    retrieval_hints: list[str]
    expected_evidence: str
    compliance_rules: str
    consequence_rules: str
    admin_config_refs: list[int]
    sort_order: int
    content_source: str = "precise_search"
    content_target: dict[str, Any] = field(default_factory=dict)
    diagnosis_mode: str = "file"
```

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/base.py backend/app/services/checklist_consequence.py backend/tests/test_checklist_consequence.py
git commit -m "feat: v2 ChecklistItemDraft markdown fields and consequence tag parser"
```

---

### Task 3: v2 Agent payload parser

**Files:**
- Modify: `backend/app/engine/checklist_agent_os.py`
- Test: `backend/tests/test_checklist_agent_os.py`

- [ ] **Step 1: Add v2 fixture + failing test**

Add to `backend/tests/test_checklist_agent_os.py`:

```python
V2_PAYLOAD = {
    "schema_version": "2",
    "items": [
        {
            "id": "item_001",
            "category_id": "cat_001",
            "title": "报价表签章",
            "requirement": "必须签字盖章",
            "technique": "检查报价表签章页",
            "importance": "high",
            "diagnosis_mode": "offline",
            "source_citations": "- 章节：第三章",
            "expected_evidence": "- 签章页",
            "compliance_rules": "## 满足\n齐全",
            "consequence_rules": "[bid_unusable]\n否决",
            "sort_order": 1,
        }
    ],
}


def test_parse_checklist_payload_v2_items_only():
    draft = parse_checklist_payload(V2_PAYLOAD)
    assert draft.schema_version == "2"
    assert len(draft.categories) == 6
    assert draft.categories[0].id == "cat_001"
    assert len(draft.items) == 1
    item = draft.items[0]
    assert item.source_citations.startswith("- 章节")
    assert item.expected_evidence == "- 签章页"
    assert item.compliance_rules.startswith("## 满足")
    assert item.retrieval_hints  # auto-generated from title


def test_parse_v2_rejects_unknown_category():
    bad = {**V2_PAYLOAD, "items": [{**V2_PAYLOAD["items"][0], "category_id": "cat_999"}]}
    with pytest.raises(ChecklistAgentResponseError, match="category_id"):
        parse_checklist_payload(bad)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_agent_os.py::test_parse_checklist_payload_v2_items_only -v`

- [ ] **Step 3: Implement parse v2**

In `checklist_agent_os.py`:

1. Import `FIXED_CATEGORY_IDS`, `fixed_categories_draft` from `checklist_categories`.
2. At start of `parse_checklist_payload`, read `schema_version`; if `"2"`, call new `_parse_v2_payload`.
3. `_parse_v2_payload` logic:
   - Require non-empty `items` list; ignore/forbid top-level `categories`.
   - Validate each `category_id in FIXED_CATEGORY_IDS`.
   - Require non-empty str for markdown fields.
   - Auto `retrieval_hints = [title]` if empty after strip.
   - Return `ChecklistDraft(schema_version="2", categories=fixed_categories_draft(), items=..., raw_response=payload)`.
4. Keep v1 path temporarily for test fixtures that still use v1 (or migrate all tests in Task 8).

Helper:

```python
def _require_markdown(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChecklistAgentResponseError(f"missing or empty {field}")
    return value.strip()
```

- [ ] **Step 4: Run parser tests**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_agent_os.py -v`

Fix remaining v1 tests by updating fixtures to v2 OR gate v1 tests behind explicit `schema_version: "1"` branch.

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/checklist_agent_os.py backend/tests/test_checklist_agent_os.py
git commit -m "feat: parse Agent OS checklist payload schema v2"
```

---

### Task 4: merge + validate v2

**Files:**
- Modify: `backend/app/engine/checklist_merge.py`
- Modify: `backend/app/services/checklist_service.py`
- Test: `backend/tests/test_checklist_merge.py`, `backend/tests/test_checklist_service.py`

- [ ] **Step 1: Update merge test fixture to v2 item shape**

Update `backend/tests/test_checklist_merge.py` helper `_item()`:

```python
def _item(item_id, category_id, title, requirement, **kwargs):
    return ChecklistItemDraft(
        id=item_id,
        category_id=category_id,
        title=title,
        requirement=requirement,
        technique=kwargs.get("technique", "对照"),
        importance=kwargs.get("importance", "medium"),
        source_citations=kwargs.get("source_citations", "- 章节：正文"),
        retrieval_hints=kwargs.get("retrieval_hints", [title]),
        expected_evidence=kwargs.get("expected_evidence", f"- {title}"),
        compliance_rules=kwargs.get("compliance_rules", "## 满足\n符合"),
        consequence_rules=kwargs.get("consequence_rules", "[general_risk]\n风险"),
        admin_config_refs=[],
        sort_order=kwargs.get("sort_order", 1),
        diagnosis_mode=kwargs.get("diagnosis_mode", "file"),
    )
```

Add test:

```python
def test_merge_groups_by_fixed_category_id():
    # two drafts with same cat_001 items dedupe; cat_004 separate
    ...
```

- [ ] **Step 2: Refactor merge**

In `checklist_merge.py`:

1. Import `fixed_categories_draft`, build `id_by_name = {c.id: c.name for c in fixed_categories_draft()}` — actually merge by **category_id** directly, not name.
2. Replace `local_name[item.category_id]` with using `item.category_id` as bucket key mapped to fixed category name via lookup dict from `fixed_categories_draft()`.
3. When splitting oversized buckets, use first line of `source_citations` or `title` prefix instead of `source_references[0].section`.
4. `final_categories` always assign global ids via existing `category-{n:03d}` pattern but preserve fixed semantic names from `cat_xxx` mapping.
5. Reassign item `category_id` to merged global category ids.

- [ ] **Step 3: Refactor validate_draft**

In `checklist_service.py`:

1. Remove `_validate_source_reference` and all calls.
2. Replace list/dict checks with markdown string non-empty checks for `source_citations`, `expected_evidence`, `compliance_rules`, `consequence_rules`.
3. Validate `item.category_id` against `FIXED_CATEGORY_IDS` **before** merge global id rewrite — note: after merge, category ids become `category-001`; validate at parse time only, post-merge validate global category references still valid.
4. Remove `_require_rules` / `_COMPLIANCE_KEYS` object validation for v2.
5. Optional: validate offline items have non-empty `## 证据不足` section or non-「无」insufficient content — keep simple: only require non-empty markdown.

- [ ] **Step 4: Run tests**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_merge.py tests/test_checklist_service.py -v`

Update all broken fixtures in `test_checklist_service.py` to v2 field shapes (large but mechanical).

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/checklist_merge.py backend/app/services/checklist_service.py backend/tests/test_checklist_merge.py backend/tests/test_checklist_service.py
git commit -m "feat: merge and validate checklist schema v2"
```

---

### Task 5: Publish / load API + schemas

**Files:**
- Modify: `backend/app/services/checklist_service.py` (`_publish`, checklist loader)
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_checklist_api.py`

- [ ] **Step 1: Update ChecklistItemOut**

In `backend/app/schemas.py`:

```python
class ChecklistItemOut(BaseModel):
    id: str
    title: str
    requirement: str
    technique: str
    importance: str
    source_citations: str = ""
    retrieval_hints: List[str]
    expected_evidence: str = ""
    compliance_rules: str = ""
    consequence_rules: str = ""
    admin_config_refs: List[int]
    content_source: str = "precise_search"
    content_target: dict = {}
    diagnosis_mode: str = "file"
    sort_order: int
    schema_version: str = "2"  # optional per-item hint; prefer generation-level
```

Remove old `source_references: List[dict]` etc.

- [ ] **Step 2: Publish stores markdown strings**

In `_publish` / item insert:

```python
source_references=item.source_citations,  # DB column reuse
expected_evidence=item.expected_evidence,  # plain text, not json.dumps list
compliance_rules=item.compliance_rules,
consequence_rules=item.consequence_rules,
retrieval_hints=json.dumps(item.retrieval_hints, ensure_ascii=False),
```

- [ ] **Step 3: Loader with v1 compat**

Add `_format_item_for_api(item, schema_version: str) -> dict`:

```python
def _format_item_for_api(item: ChecklistItem, schema_version: str) -> dict:
    if schema_version == "2":
        return {
            "source_citations": item.source_references or "",
            "expected_evidence": item.expected_evidence or "",
            "compliance_rules": item.compliance_rules or "",
            "consequence_rules": item.consequence_rules or "",
            ...
        }
    # v1 legacy: parse JSON and convert to display markdown strings
    return _legacy_item_to_markdown_api(item)
```

Implement `_legacy_item_to_markdown_api` to stringify old dict/list for read-only display.

- [ ] **Step 4: Update API tests**

Fix `backend/tests/test_checklist_api.py` assertions to expect string fields.

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_checklist_api.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py backend/app/services/checklist_service.py backend/tests/test_checklist_api.py
git commit -m "feat: checklist API publish and load schema v2 with v1 read compat"
```

---

### Task 6: Scheduler offline + batch diagnosis context

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/app/services/batch_diagnosis_context.py`
- Test: `backend/tests/test_scheduler.py` (offline path), `backend/tests/test_batch_diagnosis_agent_os.py`

- [ ] **Step 1: Offline tag parsing test**

Add to scheduler tests:

```python
def test_offline_batch_result_parses_markdown_consequence_tags():
    item = {
        "id": "item-1",
        "title": "签章",
        "requirement": "需签章",
        "consequence_rules": "[bid_unusable]\n否决",
        "diagnosis_mode": "offline",
    }
    result = _offline_batch_result(item)
    assert result.compliance == "manual_required"
    assert "bid_unusable" in result.consequence_tags
```

- [ ] **Step 2: Update scheduler**

```python
from app.services.checklist_consequence import parse_consequence_tags_from_markdown

def _offline_batch_result(item: dict):
    rules = item.get("consequence_rules") or ""
    if isinstance(rules, dict):
        tags = [k for k in rules if isinstance(k, str)]  # v1 compat
    else:
        tags = parse_consequence_tags_from_markdown(str(rules))
    ...
```

- [ ] **Step 3: Update batch diagnosis SYSTEM_INSTRUCTIONS**

In `batch_diagnosis_context.py`, replace rule 1:

```python
1. 只依据 retrieved_chunks 与检查项 requirement、compliance_rules（Markdown 判定指南）判定；禁止臆造未出现的证据。
```

Add note that `compliance_rules` uses `## 满足/违反/...` sections.

- [ ] **Step 4: Run tests**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest tests/test_scheduler.py tests/test_batch_diagnosis_agent_os.py -v -k offline`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduler.py backend/app/services/batch_diagnosis_context.py backend/tests/test_scheduler.py
git commit -m "feat: offline and batch diagnosis adapt to markdown checklist fields"
```

---

### Task 7: Frontend ChecklistReport

**Files:**
- Modify: `frontend/src/components/ChecklistReport.jsx`

- [ ] **Step 1: Replace JSON formatters**

Remove `formatRules` / `formatJson` for compliance/consequence/evidence/source.

Render:

```jsx
<pre className="checklist-md">{item.source_citations || '—'}</pre>
<pre className="checklist-md">{item.expected_evidence || '—'}</pre>
<pre className="checklist-md">{item.compliance_rules || '—'}</pre>
<pre className="checklist-md">{item.consequence_rules || '—'}</pre>
```

Update field names from `source_references` → `source_citations`.

- [ ] **Step 2: Manual smoke**

Start app, open task with v2 checklist, verify detail panel shows markdown text.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ChecklistReport.jsx
git commit -m "feat: render checklist v2 markdown fields in report UI"
```

---

### Task 8: Test fixtures + fake invoke + agent config snapshot

**Files:**
- Modify: `backend/tests/fake_checklist_invoke.py`
- Modify: `docs/agents_config/tender_checklist_generator.json`
- Modify: remaining test files flagged by full pytest

- [ ] **Step 1: Update fake_checklist_invoke to emit v2**

Return:

```python
return {
    "schema_version": "2",
    "items": [...],
}
```

No categories in response.

- [ ] **Step 2: Update agent config outputSchema**

Replace outputSchema in `tender_checklist_generator.json` with v2 items-only fields (mirror spec §4.2). Update `systemPrompt` rule 8 to say items-only + fixed category_id.

- [ ] **Step 3: Full test suite**

Run: `cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest -q`

Fix any remaining failures in `test_db.py`, `test_retrieval_*.py`, etc.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/fake_checklist_invoke.py docs/agents_config/tender_checklist_generator.json backend/tests/
git commit -m "test: migrate checklist fixtures to schema v2 and update agent config snapshot"
```

---

### Task 9: Manual Agent OS step + E2E verification

**Files:**
- Manual: Agent OS 第七步提示词（spec 附录 A）

- [ ] **Step 1: Paste appendix A prompt into Agent OS workflow final step**

User manual action — not automatable via repo.

- [ ] **Step 2: E2E checklist generation**

1. Create task with tender doc
2. Run「生成诊断项」
3. Confirm no `checklist_validation_failed` / parse errors
4. GET `/api/tasks/{id}/checklist` — 6 fixed categories, markdown item fields

- [ ] **Step 3: Document verification in PR description**

---

## Spec Coverage Self-Review

| Spec requirement | Task |
|------------------|------|
| 固定 6 分类后端注入 | Task 1, 3 |
| Agent items-only v2 | Task 3, 8 |
| Markdown 判定字段 | Task 2, 4, 5 |
| source_citations | Task 2, 3, 4, 5 |
| 去掉 offset 校验 | Task 4 |
| merge 按 category_id | Task 4 |
| 批诊断 Markdown 适配 | Task 6 |
| offline consequence 解析 | Task 2, 6 |
| 前端展示 | Task 7 |
| v1 只读兼容 | Task 5 |
| Agent config 快照 | Task 8 |
| 附录 A 提示词 | Task 9 (manual) |

No TBD placeholders in plan.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-23-checklist-v2-simplification.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每个 Task 派生子 agent，任务间 review，迭代快

**2. Inline Execution** — 本会话按 Task 顺序直接改，批次间 checkpoint

Which approach?
