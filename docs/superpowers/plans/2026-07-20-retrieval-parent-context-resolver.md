# 检索父块上下文解析（Context Resolver）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `precise_search` / `collection` 检索后处理中，用规则 + Agent OS 智能补充父块引言/兄弟块上下文，返回父块时剔除已在结果集中的子块 span，避免误判与重复。

**Architecture:** 索引期为 large 段写入 `intro_end` 偏移；rerank 后 `ContextResolver` 按结构规则生成候选动作，Agent OS `retrieval_context_resolver_app` 裁决，物化为多条带 `context_role` 的 `RetrievalHit`；替换现有 `_expand_fine_to_large` 无条件上卷逻辑。

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / SQLite / pytest / Agent OS `POST /v1/apps/invoke` / jieba

**Spec:** `docs/superpowers/specs/2026-07-20-retrieval-parent-context-resolver-design.md`

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `backend/app/config.py` | `RETRIEVAL_PARENT_MAX_CHARS` / `RETRIEVAL_SIBLING_WINDOW` / app name |
| `backend/app/models.py` | `KnowledgeChunk.intro_end` nullable column |
| `backend/app/services/retrieval/types.py` | `SegmentDraft.intro_end` |
| `backend/app/services/retrieval/segments.py` | 物化 large 时计算 `intro_end` |
| `backend/app/services/retrieval/persist.py` | 写入 / 读取 `intro_end` |
| `backend/app/engine/base.py` | `RetrievalHit` 增加 `context_role` / `derived_from` / `anchor_chunk_id` |
| `backend/app/services/retrieval/context_resolver.py` | 规则预筛、文本切分、兄弟窗口、入口 `resolve_context` |
| `backend/app/services/retrieval/context_resolver_agent_os.py` | Agent OS 适配器 |
| `backend/app/services/retrieval/provider.py` | `_precise_search` / `_collection` 接入 Resolver |
| `backend/app/services/retrieval/debug_types.py` | debug hit 透传新字段 |
| `backend/app/services/retrieval/debug.py` | trace 增加 `context_resolutions` |
| `backend/tests/stubs/retrieval_ai.py` | `StubContextResolver` + factory patch |
| `backend/tests/fixtures/retrieval_qualification.md` | 子公司 + 授权书夹具 |
| `backend/tests/test_retrieval_segments.py` | `intro_end` 物化测试 |
| `backend/tests/test_retrieval_context_resolver.py` | 规则 / 切分 / 窗口单测 |
| `backend/tests/test_retrieval_context_resolver_agent_os.py` | Agent 适配器单测 |
| `backend/tests/test_retrieval_provider_modes.py` | 集成：precise_search / collection |
| `docs/agents_config/retrieval_context_resolver.json` | Agent OS 契约快照 |
| `frontend/src/components/knowledge/ChunkDetailDrawer.jsx` | debug 命中展示 context 元数据（可选） |

---

### Task 1: `intro_end` 索引字段（TDD）

**Files:**
- Modify: `backend/app/services/retrieval/types.py`
- Modify: `backend/app/services/retrieval/segments.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/services/retrieval/persist.py`
- Test: `backend/tests/test_retrieval_segments.py`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_retrieval_segments.py` 末尾追加：

```python
def test_materialize_large_sets_intro_end_to_first_child_start():
    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree, max_chars=4000)
    segments = materialize_segments(md, tree, fine_src)

    tech = next(
        s for s in segments
        if s.segment_level == "large" and "技术方案" in s.title_path
    )
    assert tech.intro_end is not None
    assert tech.intro_end > tech.start
    assert md[tech.start : tech.intro_end] in tech.text
    # intro 不应包含第一个子章节标题正文
    assert "架构正文甲" not in md[tech.start : tech.intro_end]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/tongqianni/xlab/tender_application
.venv/bin/python -m pytest backend/tests/test_retrieval_segments.py::test_materialize_large_sets_intro_end_to_first_child_start -v
```

Expected: FAIL — `SegmentDraft` has no attribute `intro_end`

- [ ] **Step 3: 实现 `intro_end`**

`backend/app/services/retrieval/types.py` — 在 `SegmentDraft` 增加：

```python
intro_end: int | None = None
```

`backend/app/services/retrieval/segments.py` — 在 large 段构建循环内，`children = node.get("children") or []` 之后：

```python
intro_end: int | None = None
if children:
    first_child_start = int(children[0]["start_offset"])
    if first_child_start > start:
        intro_end = first_child_start
```

写入 `SegmentDraft(..., intro_end=intro_end)`。

`backend/app/models.py` — 在 `KnowledgeChunk.end` 后增加：

```python
intro_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
```

`backend/app/services/retrieval/persist.py` — `write_segments` 的 `KnowledgeChunk(...)` 增加 `intro_end=seg.intro_end`。

- [ ] **Step 4: 运行测试确认通过**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_segments.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/types.py \
  backend/app/services/retrieval/segments.py \
  backend/app/models.py \
  backend/app/services/retrieval/persist.py \
  backend/tests/test_retrieval_segments.py
git commit -m "$(cat <<'EOF'
feat: persist intro_end offset on large knowledge segments

EOF
)"
```

---

### Task 2: 扩展 `RetrievalHit` 与配置项

**Files:**
- Modify: `backend/app/engine/base.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/retrieval/provider.py`（`_chunk_to_hit`）
- Test: `backend/tests/test_retrieval_context_resolver.py`（新建，仅 dataclass  smoke）

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_retrieval_context_resolver.py`:

```python
from app.engine.base import RetrievalHit


def test_retrieval_hit_context_fields_default():
    hit = RetrievalHit(
        chunk_id="chk_a",
        file_id="f1",
        node_id="n1",
        segment_level="fine",
        title="t",
        summary="s",
        title_path=["a"],
        tags=[],
    )
    assert hit.context_role == "matched"
    assert hit.derived_from is None
    assert hit.anchor_chunk_id is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py::test_retrieval_hit_context_fields_default -v
```

Expected: FAIL — unexpected keyword or missing defaults

- [ ] **Step 3: 实现字段与配置**

`backend/app/engine/base.py` — `RetrievalHit` 增加：

```python
context_role: str = "matched"
derived_from: str | None = None
anchor_chunk_id: str | None = None
```

`backend/app/config.py` 追加：

```python
RETRIEVAL_PARENT_MAX_CHARS = 10_000
RETRIEVAL_SIBLING_WINDOW = 2
RETRIEVAL_CONTEXT_RESOLVER_APP_NAME = "retrieval_context_resolver_app"
```

`backend/app/services/retrieval/provider.py` — `_chunk_to_hit` 增加可选参数并透传：

```python
def _chunk_to_hit(
    chunk: KnowledgeChunk,
    *,
    text: str | None = None,
    context_role: str = "matched",
    derived_from: str | None = None,
    anchor_chunk_id: str | None = None,
    ...
) -> RetrievalHit:
    ...
    return RetrievalHit(
        ...
        context_role=context_role,
        derived_from=derived_from,
        anchor_chunk_id=anchor_chunk_id,
    )
```

- [ ] **Step 4: 运行测试**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py::test_retrieval_hit_context_fields_default -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/base.py backend/app/config.py \
  backend/app/services/retrieval/provider.py \
  backend/tests/test_retrieval_context_resolver.py
git commit -m "$(cat <<'EOF'
feat: add RetrievalHit context metadata and resolver config

EOF
)"
```

---

### Task 3: 文本切分与 span 剔除工具函数（TDD）

**Files:**
- Create: `backend/app/services/retrieval/context_resolver.py`（纯函数部分）
- Test: `backend/tests/test_retrieval_context_resolver.py`

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_retrieval_context_resolver.py`：

```python
from app.services.retrieval.context_resolver import (
    materialize_parent_intro,
    materialize_parent_body,
    merge_spans,
    subtract_spans,
)


def test_materialize_parent_intro_slices_markdown():
    md = "INTRO\n\n## Child\nbody"
    text = materialize_parent_intro(md, start=0, intro_end=7)
    assert text == "INTRO\n"


def test_subtract_spans_removes_child_ranges():
    md = "AAAchildBBBchild2CCC"
    full = md
    removed = subtract_spans(full, merge_spans([(3, 8), (11, 17)]))
    assert removed == "AAACCC"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py -k "intro or subtract" -v
```

Expected: FAIL — import error

- [ ] **Step 3: 实现纯函数**

`backend/app/services/retrieval/context_resolver.py` 初始内容：

```python
from __future__ import annotations


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s[0])
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    cursor = 0
    for start, end in merge_spans(spans):
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def materialize_parent_intro(markdown: str, *, start: int, intro_end: int) -> str:
    return markdown[start:intro_end]


def materialize_parent_body(
    markdown: str,
    *,
    start: int,
    end: int,
    exclude_spans: list[tuple[int, int]],
) -> str:
    body = markdown[start:end]
    # map absolute spans to body-relative
    rel = [(s - start, e - start) for s, e in exclude_spans if e > start and s < end]
    rel = [(max(0, s), min(len(body), e)) for s, e in rel]
    return subtract_spans(body, rel)
```

- [ ] **Step 4: 运行测试**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py -k "intro or subtract" -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/context_resolver.py \
  backend/tests/test_retrieval_context_resolver.py
git commit -m "$(cat <<'EOF'
feat: add parent intro/body text materialization helpers

EOF
)"
```

---

### Task 4: 规则预筛与同父兄弟窗口（TDD）

**Files:**
- Modify: `backend/app/services/retrieval/context_resolver.py`
- Test: `backend/tests/test_retrieval_context_resolver.py`

- [ ] **Step 1: 写失败测试**

```python
from app.services.retrieval.context_resolver import (
    rule_candidates,
    sibling_window,
)


def test_rule_candidates_r1_add_parent_intro():
    candidates = rule_candidates(
        intro_end=100,
        large_start=0,
        parent_body_chars=50,
        sibling_fine_count_under_parent=1,
        keyword_overlap=True,
    )
    assert "add_parent_intro" in candidates


def test_sibling_window_selects_neighbors():
    siblings = [
        {"chunk_id": "a", "node_id": "n1"},
        {"chunk_id": "b", "node_id": "n2"},
        {"chunk_id": "c", "node_id": "n3"},
    ]
    picked = sibling_window(siblings, anchor_node_id="n2", window=1)
    assert [s["chunk_id"] for s in picked] == ["a", "b", "c"]
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py -k "rule_candidates or sibling_window" -v
```

- [ ] **Step 3: 实现规则与窗口**

在 `context_resolver.py` 追加：

```python
from app.config import RETRIEVAL_PARENT_MAX_CHARS


def rule_candidates(
    *,
    intro_end: int | None,
    large_start: int,
    parent_body_chars: int,
    sibling_fine_count_under_parent: int,
    keyword_overlap: bool,
) -> list[str]:
    candidates: list[str] = []
    intro_chars = (intro_end - large_start) if intro_end and intro_end > large_start else 0

    if intro_chars > 0:
        candidates.append("add_parent_intro")
    if sibling_fine_count_under_parent >= 2:
        candidates.append("add_parent_body")
    if intro_chars > RETRIEVAL_PARENT_MAX_CHARS or parent_body_chars > RETRIEVAL_PARENT_MAX_CHARS:
        candidates.append("add_siblings")
    if keyword_overlap and "add_siblings" not in candidates and intro_chars > RETRIEVAL_PARENT_MAX_CHARS:
        candidates.append("add_siblings")
    if not candidates:
        candidates.append("keep_only")
    return list(dict.fromkeys(candidates))


def sibling_window(
    siblings: list[dict],
    *,
    anchor_node_id: str,
    window: int,
) -> list[dict]:
    if not siblings:
        return []
    idx = next((i for i, s in enumerate(siblings) if s["node_id"] == anchor_node_id), 0)
    lo = max(0, idx - window)
    hi = min(len(siblings), idx + window + 1)
    return siblings[lo:hi]
```

- [ ] **Step 4: 运行测试**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py -k "rule_candidates or sibling_window" -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/context_resolver.py \
  backend/tests/test_retrieval_context_resolver.py
git commit -m "$(cat <<'EOF'
feat: add context resolver rule candidates and sibling window

EOF
)"
```

---

### Task 5: `resolve_context` 入口（规则-only，无 Agent）

**Files:**
- Modify: `backend/app/services/retrieval/context_resolver.py`
- Test: `backend/tests/test_retrieval_context_resolver.py`
- Create: `backend/tests/fixtures/retrieval_qualification.md`

- [ ] **Step 1: 创建夹具 markdown**

`backend/tests/fixtures/retrieval_qualification.md`：

```markdown
# 资格证明

响应人必须是在中华人民共和国境内注册或开办的具有独立法人资格的企业或事业单位。

## 主公司授权书

本公司授权子公司参与本次投标并承担相应法律责任。

## 子公司资质

子公司营业执照编号：91310000XXXX。具备独立法人资格。
```

- [ ] **Step 2: 写集成式单元测试（mock resolver 决策）**

```python
import json
from pathlib import Path

import pytest

from app.engine.base import RetrievalHit
from app.services.retrieval.context_resolver import resolve_context

FIXTURES = Path(__file__).parent / "fixtures"


class _FixedResolver:
    async def resolve_group(self, payload, candidates):
        return {
            "actions": [a for a in candidates if a != "keep_only"],
            "sibling_chunk_ids": ["chk_auth"],
        }


@pytest.mark.asyncio
async def test_resolve_context_adds_parent_intro_and_sibling():
    # 构造 in-memory hits/chunks 字典（见 Step 3 实现所需最小结构）
    ...
    result, degraded = await resolve_context(
        query="独立法人资格 授权",
        requirement="响应人须为境内独立法人",
        matched_hits=[subsidiary_hit],
        chunk_by_id=chunks,
        markdown_by_file={"f1": FIXTURES.read_text(...)},
        resolver=_FixedResolver(),
    )
    roles = {h.context_role for h in result}
    assert "matched" in roles
    assert "parent_intro" in roles
    assert "sibling" in roles
    assert degraded is False
```

（实现 Step 3 时按实际函数签名调整测试；关键是断言三种 role 共存。）

- [ ] **Step 3: 实现 `resolve_context`**

核心逻辑：

```python
async def resolve_context(
    *,
    query: str,
    requirement: str,
    matched_hits: list[RetrievalHit],
    chunk_by_id: dict[str, KnowledgeChunk],
    markdown_by_file: dict[str, str],
    resolver: ContextResolverAgent | None = None,
) -> tuple[list[RetrievalHit], bool]:
    """Expand fine hits with parent/sibling context. Returns (hits, degraded)."""
```

流程：
1. 保留 large 直接命中（`segment_level == "large"`）原样，`context_role=matched`。
2. 按 `parent_node_id` 聚合 fine 命中。
3. 每组找最近 large 祖先，计算 `rule_candidates`。
4. 调 `resolver.resolve_group(...)` 或使用 fallback。
5. 物化 `parent_intro`（合成 id `{lg_id}::intro`）、`parent_body`（`{lg_id}::body`）、`sibling` hits。
6. 去重键 `(chunk_id, context_role)`。

Fallback（Agent 失败）：有 intro → `add_parent_intro`；R3 → 窗口内全部 siblings。

- [ ] **Step 4: 运行测试**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/context_resolver.py \
  backend/tests/fixtures/retrieval_qualification.md \
  backend/tests/test_retrieval_context_resolver.py
git commit -m "$(cat <<'EOF'
feat: implement resolve_context with rule fallback

EOF
)"
```

---

### Task 6: Agent OS 适配器（TDD）

**Files:**
- Create: `backend/app/services/retrieval/context_resolver_agent_os.py`
- Create: `backend/tests/test_retrieval_context_resolver_agent_os.py`
- Modify: `backend/app/services/retrieval/context_resolver.py`（factory `get_context_resolver`）

- [ ] **Step 1: 写失败测试**

```python
import json
import pytest

from app.engine.base import RetrievalHit
from app.services.retrieval.context_resolver_agent_os import AgentOSContextResolver


@pytest.mark.asyncio
async def test_agent_os_context_resolver_parses_actions():
    captured = {}

    async def fake_invoke(app_name, payload):
        captured["app"] = app_name
        return {
            "actions_json": json.dumps(["add_parent_intro", "add_siblings"]),
            "sibling_chunk_ids_json": json.dumps(["chk_auth"]),
        }

    resolver = AgentOSContextResolver(invoke_app=fake_invoke)
    out = await resolver.resolve_group(
        {
            "requirement": "独立法人",
            "query": "授权",
            "hits": [],
            "parent": {"chunk_id": "lg_q"},
            "siblings": [{"chunk_id": "chk_auth"}],
            "candidates": ["add_parent_intro", "add_siblings"],
        },
        ["add_parent_intro", "add_siblings"],
    )
    assert out["actions"] == ["add_parent_intro", "add_siblings"]
    assert out["sibling_chunk_ids"] == ["chk_auth"]
    assert captured["app"] == "retrieval_context_resolver_app"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver_agent_os.py -v
```

- [ ] **Step 3: 实现适配器**

镜像 `rerank_agent_os.py` 模式：解析 `actions_json` / `sibling_chunk_ids_json`；校验为 candidates 子集；失败抛 `ContextResolverResponseError`。

`context_resolver.py` 增加：

```python
def get_context_resolver():
    from app.services.retrieval.context_resolver_agent_os import AgentOSContextResolver
    return AgentOSContextResolver()
```

- [ ] **Step 4: 运行测试**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_context_resolver_agent_os.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/context_resolver_agent_os.py \
  backend/app/services/retrieval/context_resolver.py \
  backend/tests/test_retrieval_context_resolver_agent_os.py
git commit -m "$(cat <<'EOF'
feat: add Agent OS context resolver adapter

EOF
)"
```

---

### Task 7: 发布 Agent OS 应用并落盘契约

**Files:**
- Create: `docs/agents_config/retrieval_context_resolver.json`
- Skill: `.cursor/skills/agent-create-publish/SKILL.md`

- [ ] **Step 1: 发布 `retrieval_context_resolver_app`**

应用契约要点：
- input: `payload_json` (string) — 含 requirement/query/hits/parent/siblings/candidates
- output: `actions_json` (string), `sibling_chunk_ids_json` (string), 可选 `reason`
- systemPrompt：根据招标诊断检索场景，从 candidates 中选 actions；sibling_chunk_ids 必须是输入 siblings 的子集；子公司资质类 query 应补授权兄弟块与父引言

- [ ] **Step 2: 写入 `docs/agents_config/retrieval_context_resolver.json`**

- [ ] **Step 3: Commit**

```bash
git add docs/agents_config/retrieval_context_resolver.json
git commit -m "$(cat <<'EOF'
docs: persist retrieval context resolver Agent OS config

EOF
)"
```

---

### Task 8: 接入 `provider._precise_search` 与 `_collection`

**Files:**
- Modify: `backend/app/services/retrieval/provider.py`
- Modify: `backend/tests/stubs/retrieval_ai.py`
- Modify: `backend/tests/test_retrieval_provider_modes.py`

- [ ] **Step 1: 写失败集成测试**

在 `test_retrieval_provider_modes.py` 新增（使用 `retrieval_qualification.md` 夹具 seed chunks）：

```python
@pytest.mark.asyncio
async def test_precise_search_context_resolver_supplements_sibling(db_session, provider):
    # seed task + chunks with intro_end on large 资格证明
    result = await provider.retrieve(
        task_id="T-CTX",
        content_source="precise_search",
        content_target={"query": "独立法人 授权"},
    )
    roles = {h.context_role for h in result.items}
    assert "matched" in roles
    assert "parent_intro" in roles or "sibling" in roles
    # 不应再无条件返回完整 large 子树作为唯一 hit
    assert not (
        len(result.items) == 1
        and result.items[0].segment_level == "large"
        and "子公司" in result.items[0].text
        and "授权" in result.items[0].text
        and result.items[0].context_role == "matched"
    )
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_provider_modes.py::test_precise_search_context_resolver_supplements_sibling -v
```

- [ ] **Step 3: 修改 provider**

在 `_precise_search` 中，删除 `fine_to_expanded` / `_expand_fine_to_large` 块，替换为：

```python
from app.services.retrieval.context_resolver import resolve_context, get_context_resolver

expanded_hits, resolver_degraded = await resolve_context(
    query=query or fts_query,
    requirement=query or fts_query,
    matched_hits=candidate_hits,
    chunk_by_id=chunk_by_id,  # 需加载 task 全量 chunks
    markdown_by_file=...,       # 按需加载 md_path
    resolver=get_context_resolver(),
)
final_hits = expanded_hits
degraded = resolver_degraded
```

`_collection` 同理：fine 标签命中后不再 `_expand_fine_to_large`，改调 `resolve_context`（query 可用 tag 名拼接）。

移除或保留 `_expand_fine_to_large` 仅作测试辅助（若测试仍引用则标记 deprecated）。

- [ ] **Step 4: Stub 注册**

`backend/tests/stubs/retrieval_ai.py` 增加 `StubContextResolver`：

```python
class StubContextResolver:
    async def resolve_group(self, payload, candidates):
        actions = [a for a in candidates if a in ("add_parent_intro", "add_siblings")]
        sibling_ids = [s["chunk_id"] for s in payload.get("siblings", [])]
        return {"actions": actions or ["keep_only"], "sibling_chunk_ids": sibling_ids[:1]}
```

在 `apply_retrieval_ai_stubs` patch `get_context_resolver`。

- [ ] **Step 5: 运行相关测试**

```bash
.venv/bin/python -m pytest backend/tests/test_retrieval_provider_modes.py backend/tests/test_retrieval_context_resolver.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/retrieval/provider.py \
  backend/tests/stubs/retrieval_ai.py \
  backend/tests/test_retrieval_provider_modes.py
git commit -m "$(cat <<'EOF'
feat: wire context resolver into precise_search and collection

EOF
)"
```

---

### Task 9: Debug 台透传与 trace

**Files:**
- Modify: `backend/app/services/retrieval/debug_types.py`
- Modify: `backend/app/services/retrieval/debug.py`
- Modify: `frontend/src/components/knowledge/ChunkDetailDrawer.jsx`（若 debug 结果复用该组件则改对应 debug 列表组件）

- [ ] **Step 1: 后端 hit_dict 增加字段**

`debug_types.py` 的 `hit_dict` 追加：

```python
"context_role": h.context_role,
"derived_from": h.derived_from,
"anchor_chunk_id": h.anchor_chunk_id,
```

`DebugTrace` 增加 `context_resolutions: list[dict]`；`debug.py` 在 precise 路径记录每组 parent/actions/sibling_ids。

- [ ] **Step 2: 前端 debug 命中列表展示**

在知识调试台检索结果表格增加列 `context_role`；抽屉展示 `derived_from` / `anchor_chunk_id`（只读文本）。

- [ ] **Step 3: 手动验证**

启动后端，在知识调试台对「独立法人 授权」查询，确认三条 hit 与 trace。

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/retrieval/debug_types.py \
  backend/app/services/retrieval/debug.py \
  frontend/src/components/knowledge/ChunkDetailDrawer.jsx
git commit -m "$(cat <<'EOF'
feat: expose context resolver fields in knowledge debug console

EOF
)"
```

---

### Task 10: 全量回归与文档

**Files:**
- Modify: `docs/superpowers/specs/2026-07-17-workspace-document-retrieval-design.md`（§4.3 注明已被新 spec supersede）

- [ ] **Step 1: 运行全量 backend 测试**

```bash
.venv/bin/python -m pytest backend/tests/ -v --tb=short
```

Expected: 全部 PASS；若有旧测试断言「fine 展开为完整 large」，按新行为更新断言。

- [ ] **Step 2: 更新旧 spec 交叉引用**

在 `2026-07-17-workspace-document-retrieval-design.md` §4.3 首段追加：

> **注（2026-07-20）：** `precise_search` / `collection` 的 fine→large 展开行为已由 `2026-07-20-retrieval-parent-context-resolver-design.md` 替代。

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-17-workspace-document-retrieval-design.md
git commit -m "$(cat <<'EOF'
docs: note context resolver supersedes fine-to-large expansion

EOF
)"
```

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| `intro_end` 索引字段 | Task 1 |
| `RetrievalHit` context 元数据 | Task 2 |
| parent_intro / parent_body 切分 | Task 3 |
| 规则 R1–R4 + 兄弟窗口 | Task 4 |
| `resolve_context` 入口 + 去重 | Task 5 |
| Agent OS 裁决 + fallback + degraded | Task 6–7 |
| precise_search / collection 接入 | Task 8 |
| 子公司+授权夹具回归 | Task 5, 8 |
| debug 台透传 | Task 9 |
| 配置项 10000 / window 2 | Task 2 |
| large 直接命中保持原样 | Task 5（resolve_context 内跳过 large matched 的再展开） |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-retrieval-parent-context-resolver.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — 每个 Task 派发独立 subagent，Task 间做 review，迭代快
2. **Inline Execution** — 本会话按 Task 顺序执行，批次间设 checkpoint 供你 review

你选哪种方式开始实现？
