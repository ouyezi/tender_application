# 工作区文档内容查找 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为工作区文档构建双粒度本地索引（含 OCR/表格/受控标签/Wiki），并实现按 `content_source` 分流的真实 `RetrievalProvider`，供诊断精准召回。

**Architecture:** 解析成功后由 `IndexScheduler` 异步建索引：物化 fine/large 知识块 →（Mock 或 Agent OS）富化打标 → FTS5 + 本地向量 → 预生成 Wiki。诊断侧 `WorkspaceRetrievalProvider` 按 `full_document | collection | large_segments | precise_search` 分流；仅精确查找走重写/三路召回/重排。首版 Agent 调用提供 Protocol + Mock，并预留 Agent OS 适配器（可复用解读计划中的 `AgentOSClient`，若尚未落地则本计划内先放 Mock）。

**Tech Stack:** FastAPI、SQLAlchemy、aiosqlite、SQLite FTS5、jieba（中文分词）、PyMuPDF + pytesseract（OCR）、numpy（本地向量余弦检索）、httpx/Agent OS（可选）、pytest。

**Spec:** `docs/superpowers/specs/2026-07-17-workspace-document-retrieval-design.md`

**增量交付顺序（每个 Task 结束后应可测）：**

1. 类型与双粒度物化  
2. 索引表 + IndexScheduler（无 AI）  
3. 受控标签 + Mock 富化  
4. 三类直取分流（全文/集合/大段）  
5. FTS5 关键字  
6. 本地 Embedding + 向量召回  
7. `precise_search` 完整链路（Mock 重写/AI 重排）  
8. OCR + 表格入索引  
9. Wiki + 接线诊断调度器 + checklist 字段  

---

## File Structure

```text
backend/app/
  config.py                              # retrieval / OCR / embedding / tag knobs
  models.py                              # KnowledgeChunk, KnowledgeTag, WikiPage, IndexJob
  db.py                                  # ensure FTS virtual table / seed tags
  engine/
    base.py                              # RetrievalResult, ContentSource, extend RetrievalProvider
    retrieval_mock.py                    # keep for unit tests of batch engine
    retrieval_workspace.py               # NEW WorkspaceRetrievalProvider
  services/
    retrieval/
      __init__.py                        # NEW
      types.py                           # NEW dataclasses / constants
      tags.py                            # NEW controlled vocabulary helpers
      segments.py                        # NEW fine+large materialization from tree/chunks
      table_text.py                      # NEW HTML/table → searchable text
      persist.py                         # NEW write/read knowledge_chunks
      fts.py                             # NEW FTS5 index build/search
      vectors.py                         # NEW local embed + cosine search
      enricher.py                        # NEW ChunkEnricher protocol + Mock
      wiki.py                            # NEW WikiBuilder protocol + Mock
      rewrite.py                         # NEW QueryRewriter protocol + Mock
      rerank.py                          # NEW AiReranker protocol + Mock
      provider.py                        # NEW routing + precise_search pipeline
    index_scheduler.py                   # NEW async index worker
    parse/
      convert.py                         # OCR hook for image-only PDF pages
      ocr.py                             # NEW page OCR helper
      pipeline.py                        # wire OCR + enqueue index
      chunk.py                           # unchanged unless table merge needs helper
    parse_scheduler.py                   # after parse success → index_scheduler.kick
    scheduler.py                         # use WorkspaceRetrievalProvider when configured

backend/tests/
  test_retrieval_segments.py             # NEW dual granularity + parent expand
  test_retrieval_tags.py                 # NEW controlled tags
  test_index_scheduler.py                # NEW index job lifecycle / partial
  test_retrieval_provider_modes.py       # NEW four content_source modes
  test_retrieval_fts.py                  # NEW FTS5
  test_retrieval_vectors.py              # NEW embedding search
  test_retrieval_precise.py              # NEW rewrite + merge + rerank + degrade
  test_parse_ocr.py                      # NEW OCR pages
  test_table_text.py                     # NEW table indexing
  test_scheduler.py                      # switch provider wiring
  test_migrate_schema.py                 # new tables/columns
  fixtures/                              # small md/tree/chunks samples

backend/requirements.txt                 # + jieba, pytesseract, numpy, Pillow

docs/agents_config/                      # optional later: enricher/rewrite/rerank apps
```

---

### Task 1: 检索类型与双粒度物化

**Files:**
- Create: `backend/app/services/retrieval/__init__.py`
- Create: `backend/app/services/retrieval/types.py`
- Create: `backend/app/services/retrieval/segments.py`
- Test: `backend/tests/test_retrieval_segments.py`
- Create: `backend/tests/fixtures/retrieval_sample_tree.json`
- Create: `backend/tests/fixtures/retrieval_sample.md`

- [ ] **Step 1: Write failing tests for fine + large materialization**

Create `backend/tests/fixtures/retrieval_sample.md`:

```markdown
# 技术方案

引言段落。

## 架构设计

架构正文甲。

## 实施计划

实施正文乙。
```

Create `backend/tests/fixtures/retrieval_sample_tree.json` by running the existing `build_document_tree` once in a scratch test, or hand-author a minimal tree with parent `技术方案` and two leaves, each with correct `start_offset`/`end_offset`/`subtree_end` matching the markdown file. Prefer generating in the test:

```python
from pathlib import Path

from app.services.parse.tree import build_document_tree
from app.services.parse.chunk import chunk_from_tree
from app.services.retrieval.segments import materialize_segments

FIXTURES = Path(__file__).parent / "fixtures"


def test_materialize_fine_and_large_for_parent_section():
    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree, max_chars=4000)
    segments = materialize_segments(md, tree, fine_src)

    fines = [s for s in segments if s.segment_level == "fine"]
    larges = [s for s in segments if s.segment_level == "large"]
    assert len(fines) >= 2
    assert any(s.title_path[-1] == "技术方案" or "技术方案" in s.title_path for s in larges)

    tech = next(s for s in larges if s.title_path and s.title_path[0] == "技术方案" or s.title_path[-1] == "技术方案")
    assert "架构正文甲" in tech.text
    assert "实施正文乙" in tech.text
    assert len(tech.child_chunk_ids) >= 2


def test_expand_parent_hit_returns_large_not_title_only():
    md = (FIXTURES / "retrieval_sample.md").read_text(encoding="utf-8")
    tree = build_document_tree(md)
    fine_src = chunk_from_tree(md, tree)
    segments = materialize_segments(md, tree, fine_src)
    from app.services.retrieval.segments import expand_parent_hits

    # Simulate hitting the parent node id of 技术方案
    parent = next(s for s in segments if s.segment_level == "large" and "技术方案" in s.title_path)
    expanded = expand_parent_hits([parent.node_id], segments)
    assert len(expanded) == 1
    assert expanded[0].segment_level == "large"
    assert "架构正文甲" in expanded[0].text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_retrieval_segments.py -v`  
Expected: FAIL with `ModuleNotFoundError` or `materialize_segments` undefined.

- [ ] **Step 3: Implement types + materialize_segments**

`backend/app/services/retrieval/__init__.py`:

```python
"""Workspace document indexing and typed retrieval."""
```

`backend/app/services/retrieval/types.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ContentSource = Literal[
    "full_document", "collection", "large_segments", "precise_search"
]
SegmentLevel = Literal["fine", "large"]
ChunkSource = Literal["native_text", "ocr", "table"]


@dataclass
class SegmentDraft:
    chunk_id: str
    node_id: str
    parent_node_id: str | None
    ancestor_node_ids: list[str]
    segment_level: SegmentLevel
    title_path: list[str]
    start: int
    end: int
    text: str
    child_chunk_ids: list[str] = field(default_factory=list)
    source: ChunkSource = "native_text"
    title: str = ""
    summary: str = ""
    description: str = ""
    tags: list[dict[str, Any]] = field(default_factory=list)  # {name, confidence}
```

`backend/app/services/retrieval/segments.py`:

```python
from __future__ import annotations

from typing import Any

from app.services.retrieval.types import SegmentDraft


def _index_nodes(nodes: list[dict[str, Any]], id_map: dict[str, dict[str, Any]]) -> None:
    for node in nodes:
        id_map[node["id"]] = node
        _index_nodes(node.get("children") or [], id_map)


def _ancestors(node_id: str, id_map: dict[str, dict[str, Any]]) -> list[str]:
    out: list[str] = []
    cur = id_map.get(node_id)
    seen = {node_id}
    while cur and cur.get("parent_id") and cur["parent_id"] not in seen:
        pid = cur["parent_id"]
        out.append(pid)
        seen.add(pid)
        cur = id_map.get(pid)
    return out


def materialize_segments(
    markdown: str,
    tree: dict[str, Any],
    fine_chunks: list[dict[str, Any]],
) -> list[SegmentDraft]:
    id_map: dict[str, dict[str, Any]] = {}
    _index_nodes(tree.get("nodes") or [], id_map)

    fines: list[SegmentDraft] = []
    for ch in fine_chunks:
        node_id = ch["node_id"]
        node = id_map.get(node_id, {})
        fines.append(
            SegmentDraft(
                chunk_id=ch["chunk_id"],
                node_id=node_id,
                parent_node_id=node.get("parent_id"),
                ancestor_node_ids=_ancestors(node_id, id_map),
                segment_level="fine",
                title_path=list(ch.get("title_path") or []),
                start=int(ch["start"]),
                end=int(ch["end"]),
                text=ch.get("text") or markdown[ch["start"] : ch["end"]],
                title=(ch.get("title_path") or [""])[-1] if ch.get("title_path") else "",
            )
        )

    fines_by_node: dict[str, list[str]] = {}
    for f in fines:
        fines_by_node.setdefault(f.node_id, []).append(f.chunk_id)
        for anc in f.ancestor_node_ids:
            fines_by_node.setdefault(anc, []).append(f.chunk_id)

    larges: list[SegmentDraft] = []
    for node_id, node in id_map.items():
        children = node.get("children") or []
        if not children:
            continue
        start = int(node["start_offset"])
        end = int(node.get("subtree_end") or node["end_offset"])
        title_path: list[str] = []
        # rebuild title path
        cur = node
        parts = [cur["title"]]
        seen = {node_id}
        while cur.get("parent_id") and cur["parent_id"] not in seen:
            parent = id_map[cur["parent_id"]]
            parts.append(parent["title"])
            seen.add(cur["parent_id"])
            cur = parent
        title_path = list(reversed(parts))
        child_ids = list(dict.fromkeys(fines_by_node.get(node_id, [])))
        larges.append(
            SegmentDraft(
                chunk_id=f"lg_{node_id}",
                node_id=node_id,
                parent_node_id=node.get("parent_id"),
                ancestor_node_ids=_ancestors(node_id, id_map),
                segment_level="large",
                title_path=title_path,
                start=start,
                end=end,
                text=markdown[start:end],
                child_chunk_ids=child_ids,
                title=node.get("title") or "",
            )
        )

    return fines + larges


def expand_parent_hits(
    node_ids: list[str],
    segments: list[SegmentDraft],
) -> list[SegmentDraft]:
    by_node_large = {
        s.node_id: s for s in segments if s.segment_level == "large"
    }
    out: list[SegmentDraft] = []
    seen: set[str] = set()
    for nid in node_ids:
        large = by_node_large.get(nid)
        if large and large.chunk_id not in seen:
            out.append(large)
            seen.add(large.chunk_id)
    return out
```

- [ ] **Step 4: Run tests and make sure they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_retrieval_segments.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval backend/tests/test_retrieval_segments.py backend/tests/fixtures/retrieval_sample.md
git commit -m "feat: materialize fine and large retrieval segments"
```

---

### Task 2: 模型、配置、索引任务表

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/db.py`
- Modify: `backend/tests/test_migrate_schema.py`

- [ ] **Step 1: Write failing migration assertions**

Append to `backend/tests/test_migrate_schema.py` (follow existing style for column/table checks):

```python
async def test_knowledge_retrieval_tables_exist(migrated_engine):
    # use the same helper pattern as other tests in this file
    from sqlalchemy import inspect

    async with migrated_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "knowledge_chunks" in tables
    assert "knowledge_tags" in tables
    assert "wiki_pages" in tables
    assert "index_jobs" in tables
```

Adapt to the file’s actual fixtures (`client` / `init_db` / etc.). If the file uses sync inspect after `init_models`, mirror that.

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_migrate_schema.py -k knowledge -v`  
Expected: FAIL (tables missing).

- [ ] **Step 3: Add config + models**

Append to `backend/app/config.py`:

```python
RETRIEVAL_PROVIDER = "workspace"  # workspace | mock
INDEX_TAG_MIN_CONFIDENCE = 0.5
PRECISE_SEARCH_TOP_K = 20
PRECISE_SEARCH_RECALL_PER_CHANNEL = 40
EMBEDDING_MODEL_PATH = ""  # local path; empty → hash-embedding fallback for tests
EMBEDDING_DIM = 384
OCR_ENABLED = True
OCR_MIN_CHARS_PER_PAGE = 40
AGENT_CHUNK_ENRICHER = "mock"  # mock | agent_os
AGENT_QUERY_REWRITER = "mock"
AGENT_AI_RERANKER = "mock"
AGENT_WIKI_BUILDER = "mock"
```

Append models to `backend/app/models.py` (after `ParseJob`):

```python
class KnowledgeTag(Base):
    __tablename__ = "knowledge_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    aliases: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON list
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    file_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_node_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ancestor_node_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    segment_level: Mapped[str] = mapped_column(String(16), nullable=False)  # fine|large
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    title_path: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    text_inline: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    child_chunk_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="native_text")
    index_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    embedding_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WikiPage(Base):
    __tablename__ = "wiki_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    member_chunk_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IndexJob(Base):
    __tablename__ = "index_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    file_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # queued|running|partial|ready|failed
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="segments")
    # segments|enrich|fts|vectors|wiki
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

In `db.py` `init`/seed path, seed default `KnowledgeTag` rows (at minimum):

```python
DEFAULT_KNOWLEDGE_TAGS = [
    ("授权证书", ["授权书", "授权函"], "投标授权类材料"),
    ("资质证明", ["资质文件", "资质证书"], "企业/人员资质类材料"),
    ("营业执照", [], "营业执照"),
    ("售后政策", ["售后服务", "质保"], "售后与质保条款"),
    ("退款政策", ["退款", "七天无理由", "7天无理由"], "退款与无理由退货"),
]
```

Also ensure `create_all` covers new tables (existing pattern).

- [ ] **Step 4: Run migration test — PASS**

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/models.py backend/app/db.py backend/tests/test_migrate_schema.py
git commit -m "feat: add knowledge index models and tag seed"
```

---

### Task 3: 持久化片段 + IndexScheduler（无 AI）

**Files:**
- Create: `backend/app/services/retrieval/persist.py`
- Create: `backend/app/services/index_scheduler.py`
- Modify: `backend/app/services/parse_scheduler.py`（解析成功后 `index_scheduler.enqueue`）
- Modify: `backend/app/main.py`（lifespan 启动 index worker）
- Test: `backend/tests/test_index_scheduler.py`

- [ ] **Step 1: Write failing test**

```python
import pytest
from pathlib import Path

from app.services import index_scheduler
from app.models import KnowledgeChunk, IndexJob


@pytest.mark.asyncio
async def test_index_job_writes_fine_and_large(db_session, sample_parsed_workspace_file):
    """sample_parsed_workspace_file fixture: WorkspaceFile with md/tree/chunks on disk."""
    await index_scheduler.enqueue(
        sample_parsed_workspace_file.task_id,
        sample_parsed_workspace_file.id,
    )
    await index_scheduler.drain_once_for_tests()

    chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.file_id == sample_parsed_workspace_file.id
            )
        )
    ).scalars().all()
    assert any(c.segment_level == "fine" for c in chunks)
    assert any(c.segment_level == "large" for c in chunks)
    job = (
        await db_session.execute(
            select(IndexJob).where(IndexJob.file_id == sample_parsed_workspace_file.id)
        )
    ).scalar_one()
    assert job.status in {"ready", "partial"}
```

Reuse/adapt fixtures from `test_parse_scheduler.py` / `test_workspaces_api.py` for creating a parsed file.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement persist + scheduler**

`persist.py` responsibilities:

- `invalidate_file_index(session, task_id, file_id)` — delete chunks/wiki members for file
- `write_segments(session, task_id, file_id, segments, text_dir)` — for text longer than 8KB write `uploads/{task_id}/index_text/{chunk_id}.txt` and set `text_path`; else `text_inline`
- `load_chunk_text(chunk) -> str`

`index_scheduler.py` mirror `parse_scheduler`:

- `enqueue(task_id, file_id)` creates `IndexJob(status=queued)`
- worker: load WorkspaceFile paths → `materialize_segments` → persist → mark stage progress → `status=ready`（enrich/fts/vectors 后续 Task 再挂；本 Task 可先把 stage 走完 segments 即 ready，或留下 `partial` 直到后续阶段接上——**本 Task 结束后 status=`ready` 仅表示片段已可查**，后续 Task 将拆成多 stage 并允许 `partial`）

为符合「部分可查」，定义：

- 完成 segments 后：`status=partial`, `stage=enrich`（若 enrich 未实现则本 Task 直接 skip enrich 到 ready）
- 本 Task：**segments 完成后设 `ready`**，后续 Task 改成多阶段并在 enrich 前保持 `partial`。

在 `parse_scheduler._run_job` 成功分支末尾调用：

```python
from app.services import index_scheduler
await index_scheduler.enqueue(job.task_id, job.file_id)
await index_scheduler.kick()
```

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: index scheduler persists fine/large knowledge chunks"
```

---

### Task 4: 受控标签 + Mock ChunkEnricher

**Files:**
- Create: `backend/app/services/retrieval/tags.py`
- Create: `backend/app/services/retrieval/enricher.py`
- Modify: `backend/app/services/index_scheduler.py`（enrich stage）
- Test: `backend/tests/test_retrieval_tags.py`

- [ ] **Step 1: Failing tests**

```python
from app.services.retrieval.tags import map_to_controlled_tags, validate_target_tags
from app.services.retrieval.enricher import MockChunkEnricher
from app.services.retrieval.types import SegmentDraft


def test_validate_target_tags_rejects_unknown():
    allowed = {"授权证书", "资质证明"}
    ok, err = validate_target_tags(["授权证书", "随便"], allowed)
    assert ok is False
    assert "随便" in err


def test_map_aliases_to_canonical():
    tags = map_to_controlled_tags(
        ["授权书", "七天无理由"],
        catalog=[
            {"name": "授权证书", "aliases": ["授权书"]},
            {"name": "退款政策", "aliases": ["七天无理由", "7天无理由"]},
        ],
    )
    names = {t["name"] for t in tags}
    assert names == {"授权证书", "退款政策"}


@pytest.mark.asyncio
async def test_mock_enricher_assigns_tags_from_keywords():
    enricher = MockChunkEnricher()
    seg = SegmentDraft(
        chunk_id="c1",
        node_id="n1",
        parent_node_id=None,
        ancestor_node_ids=[],
        segment_level="fine",
        title_path=["授权证书"],
        start=0,
        end=10,
        text="兹授权某某公司作为投标授权代表。",
        title="授权证书",
    )
    out = await enricher.enrich_many(
        task_id="T-1",
        segments=[seg],
        catalog=[{"name": "授权证书", "aliases": ["授权代表"]}],
    )
    assert out[0].tags
    assert out[0].tags[0]["name"] == "授权证书"
    assert out[0].summary
```

- [ ] **Step 2: Implement**

`tags.py`:

```python
def validate_target_tags(target_tags: list[str], allowed: set[str]) -> tuple[bool, str]:
    bad = [t for t in target_tags if t not in allowed]
    if bad:
        return False, f"非法标签: {bad}; 合法: {sorted(allowed)}"
    return True, ""


def map_to_controlled_tags(
    raw_labels: list[str],
    *,
    catalog: list[dict],
    default_confidence: float = 0.8,
) -> list[dict]:
    alias_map: dict[str, str] = {}
    for row in catalog:
        alias_map[row["name"]] = row["name"]
        for a in row.get("aliases") or []:
            alias_map[a] = row["name"]
    out = []
    seen = set()
    for label in raw_labels:
        name = alias_map.get(label) or alias_map.get(label.strip())
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "confidence": default_confidence})
    return out
```

`MockChunkEnricher.enrich_many`: 用标题路径 + 正文关键词命中 catalog 别名；`summary = text[:120]`；`description = title_path 拼接`。

Index scheduler：segments 后调用 enricher，写回 `KnowledgeChunk.title/summary/description/tags`，`index_status=ready`。

- [ ] **Step 3: Tests PASS → Commit**

```bash
git commit -m "feat: mock chunk enricher with controlled tag mapping"
```

---

### Task 5: 扩展 Retrieval 协议 + 三类直取分流

**Files:**
- Modify: `backend/app/engine/base.py`
- Create: `backend/app/services/retrieval/provider.py`
- Create: `backend/app/engine/retrieval_workspace.py`（薄封装，实现 `RetrievalProvider`）
- Test: `backend/tests/test_retrieval_provider_modes.py`

- [ ] **Step 1: Extend base types**

In `backend/app/engine/base.py` add:

```python
@dataclass
class RetrievalHit:
    chunk_id: str
    file_id: str
    node_id: str
    segment_level: str
    title: str
    summary: str
    title_path: list[str]
    tags: list[dict]
    text: str = ""
    child_chunk_ids: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class RetrievalResult:
    mode: str
    items: list[RetrievalHit]
    index_status: str  # ready|partial|unavailable
    incomplete: bool = False
    degraded: bool = False
    error: str | None = None


class TypedRetrievalProvider(Protocol):
    async def retrieve(
        self,
        *,
        task_id: str,
        content_source: str,
        content_target: dict[str, Any],
        item_hints: dict[str, Any] | None = None,
    ) -> RetrievalResult: ...
```

Keep existing `RetrievalProvider.retrieve_for_category`；`WorkspaceRetrievalProvider` 同时实现两者：`retrieve_for_category` 读取每个 item 的 `content_source`（若分类内混合，按 **精确查找合并** 或 **逐项 retrieve 再合并去重**——实现选择：**逐项 retrieve，按 chunk_id 去重合并为 `list[RetrievedChunk]`**，`RetrievedChunk.text` 取 hit.text，`location` 取 `"/".join(title_path)`）。

检查项 dict 约定字段（Task 9 写入 DB；本 Task 测试直接传 dict）：

```python
{
  "content_source": "collection",
  "content_target": {"target_tags": ["授权证书"]},
  "retrieval_hints": [],
  "title": "...",
}
```

- [ ] **Step 2: Failing mode tests**

```python
@pytest.mark.asyncio
async def test_full_document_returns_markdown(provider, indexed_task_with_tender):
    result = await provider.retrieve(
        task_id=indexed_task_with_tender.id,
        content_source="full_document",
        content_target={"file_role": "tender"},
    )
    assert result.mode == "full_document"
    assert len(result.items) == 1
    assert "招标" in result.items[0].text or len(result.items[0].text) > 0


@pytest.mark.asyncio
async def test_collection_filters_by_tag(provider, indexed_task_with_tagged_chunks):
    result = await provider.retrieve(
        task_id=indexed_task_with_tagged_chunks,
        content_source="collection",
        content_target={"target_tags": ["授权证书"]},
    )
    assert result.mode == "collection"
    assert result.items
    assert all(
        any(t["name"] == "授权证书" for t in hit.tags) for hit in result.items
    )


@pytest.mark.asyncio
async def test_large_segments_returns_large_only(provider, indexed_bid_task):
    result = await provider.retrieve(
        task_id=indexed_bid_task,
        content_source="large_segments",
        content_target={"file_role": "bid"},
    )
    assert result.mode == "large_segments"
    assert all(h.segment_level == "large" for h in result.items)
    assert all(h.child_chunk_ids is not None for h in result.items)


@pytest.mark.asyncio
async def test_missing_content_source_is_config_error(provider):
    result = await provider.retrieve(
        task_id="T-x",
        content_source="",
        content_target={},
    )
    assert result.error
```

- [ ] **Step 3: Implement routing in `provider.py`**

伪代码要点：

```python
async def retrieve(...):
    if content_source == "full_document":
        return await _full_document(task_id, content_target)
    if content_source == "collection":
        return await _collection(task_id, content_target)
    if content_source == "large_segments":
        return await _large_segments(task_id, content_target)
    if content_source == "precise_search":
        return await _precise_search(...)  # Task 7；本 Task 可先返回 error="not_implemented" 并单测 skip，或占位 raise
    return RetrievalResult(mode=content_source, items=[], index_status="unavailable", error="unknown content_source")
```

`_full_document`: 查 `DiagnosisTask.tender_file_id` / `bid_file_id` → `WorkspaceFile.md_path` → 读全文 → 单条 `RetrievalHit`。

`_collection`: 校验标签 → SQL/JSON 过滤 `KnowledgeChunk.tags` → 若 `segment_level=fine` 且存在同 `node_id` 的 large，**替换/展开为 large**（调用内存 `expand_parent_hits` 或 DB 查 `lg_{node_id}`）。

`_large_segments`: 过滤 `segment_level=large` + file_role；可选 `root_node_id` 用 `ancestor_node_ids`/`node_id` 过滤。

- [ ] **Step 4: PASS → Commit**

```bash
git commit -m "feat: typed retrieval for full_document collection large_segments"
```

---

### Task 6: FTS5 关键字索引

**Files:**
- Create: `backend/app/services/retrieval/fts.py`
- Modify: `backend/app/db.py`（创建虚拟表 `knowledge_chunks_fts`）
- Modify: `backend/app/services/index_scheduler.py`
- Modify: `backend/requirements.txt`（`jieba`）
- Test: `backend/tests/test_retrieval_fts.py`

- [ ] **Step 1: Add dependency**

```text
jieba>=0.42.1
```

- [ ] **Step 2: Failing test**

```python
@pytest.mark.asyncio
async def test_fts_finds_chinese_keywords(indexed_chunks_session):
    from app.services.retrieval.fts import search_fts
    hits = await search_fts(indexed_chunks_session, task_id="T-1", query="授权证书", limit=10)
    assert hits
    assert any("授权" in h["title"] or "授权" in h["snippet"] for h in hits)
```

- [ ] **Step 3: Implement**

- 建表：`CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(chunk_id, task_id, file_id, title, body, tokenize='unicode61');`
- 索引写入：对 title+text 用 jieba.cut 后空格拼接再 INSERT。
- `search_fts`：同样分词 query，`MATCH` 查询，返回 chunk_id 列表与 bm25 分数（`bm25(knowledge_chunks_fts)`）。

Index scheduler 在 enrich 后 rebuild 该 file 的 FTS 行。

- [ ] **Step 4: PASS → Commit**

```bash
git commit -m "feat: SQLite FTS5 keyword index with jieba tokenization"
```

---

### Task 7: 本地 Embedding 与向量召回

**Files:**
- Create: `backend/app/services/retrieval/vectors.py`
- Modify: `backend/requirements.txt`（`numpy`）
- Modify: `backend/app/services/index_scheduler.py`
- Test: `backend/tests/test_retrieval_vectors.py`

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_hash_embedding_retrieves_similar_chunk(tmp_path):
    from app.services.retrieval.vectors import HashEmbeddingModel, VectorIndex

    model = HashEmbeddingModel(dim=64)
    index = VectorIndex(tmp_path / "T-1")
    index.upsert([
        ("c1", model.embed("售后服务七天无理由退货")),
        ("c2", model.embed("投标报价汇总表")),
    ])
    q = model.embed("是否支持7天无理由")
    hits = index.search(q, top_k=1)
    assert hits[0][0] == "c1"
```

- [ ] **Step 2: Implement**

- `EmbeddingModel` Protocol：`embed(text) -> np.ndarray`；`embed_many(texts)`.
- `HashEmbeddingModel`：稳定哈希袋词向量（测试与无模型时的 fallback）。
- `LocalEmbeddingModel`：若 `EMBEDDING_MODEL_PATH` 非空，用 `numpy` 加载或预留 sentence-transformers 接口；**本计划默认 Hash/轻量实现可过测**，生产路径文档注明配置真实模型。
- `VectorIndex`：`uploads/{task_id}/vectors/{file_id}.npz` 存 `{chunk_ids: [], matrix: ndarray}`；`search` 余弦相似度。

Index scheduler：对 `fine` 块 embedding，更新 `embedding_status=ready`。

- [ ] **Step 3: PASS → Commit**

```bash
git commit -m "feat: local vector index with embedding fallback"
```

---

### Task 8: precise_search 链路（Mock 重写 / 三路 / BM25 / AI 重排）

**Files:**
- Create: `backend/app/services/retrieval/rewrite.py`
- Create: `backend/app/services/retrieval/rerank.py`
- Create: `backend/app/services/retrieval/wiki.py`（Mock：按标签聚合成页，供 Wiki 召回）
- Modify: `backend/app/services/retrieval/provider.py`
- Test: `backend/tests/test_retrieval_precise.py`

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.asyncio
async def test_precise_search_merges_channels_and_reranks(provider, indexed_semantic_task):
    result = await provider.retrieve(
        task_id=indexed_semantic_task,
        content_source="precise_search",
        content_target={"query": "是否支持七天无理由退货"},
        item_hints={"retrieval_hints": ["售后", "退款政策"]},
    )
    assert result.mode == "precise_search"
    assert result.items
    assert result.items[0].title  # reranked


@pytest.mark.asyncio
async def test_precise_search_degrades_when_reranker_fails(provider, monkeypatch, indexed_semantic_task):
    async def boom(*a, **k):
        raise RuntimeError("rerank down")
    monkeypatch.setattr(provider, "_ai_rerank", boom)
    result = await provider.retrieve(
        task_id=indexed_semantic_task,
        content_source="precise_search",
        content_target={"query": "退款"},
    )
    assert result.degraded is True
    assert result.items  # still has vector+fts merge
```

- [ ] **Step 2: Implement pipeline**

```text
MockQueryRewriter.rewrite(query, hints) ->
  { "vector_query": str, "keywords": list[str], "wiki_query": str }

channels:
  vector_hits = vectors.search(embed(vector_query))
  fts_hits = fts.search(" ".join(keywords))
  wiki_hits = wiki_pages matching wiki_query tags → member_chunk_ids

merge by chunk_id with channel weights (config):
  score = w_v * cos + w_f * bm25_norm + w_w * wiki_boost
sort → top PRECISE_SEARCH_TOP_K

MockAiReranker.rerank(requirement, hits[:N]) -> ordered ids
  # deterministic: prefer tag 退款政策/售后政策 then higher score

on AiReranker/QueryRewriter exception:
  return merged list, degraded=True

parent expansion: if hit node has large, replace with large (spec §4.3)
```

Wiki Mock builder in index scheduler：按 tag 分组 fine chunks，写 `WikiPage`。

- [ ] **Step 3: PASS → Commit**

```bash
git commit -m "feat: precise_search with three-channel recall and mock AI rerank"
```

---

### Task 9: OCR 与表格入索引

**Files:**
- Create: `backend/app/services/parse/ocr.py`
- Modify: `backend/app/services/parse/convert.py`
- Create: `backend/app/services/retrieval/table_text.py`
- Modify: `backend/app/services/parse/pipeline.py` 或 `index_scheduler`（表格合并）
- Modify: `backend/requirements.txt`（`pytesseract`, `Pillow`）
- Test: `backend/tests/test_parse_ocr.py`, `backend/tests/test_table_text.py`

- [ ] **Step 1: Table text test**

```python
from app.services.retrieval.table_text import html_table_to_text

def test_html_table_to_text_flattens_cells():
    html = "<table><tr><td>授权单位</td><td>某某公司</td></tr></table>"
    text = html_table_to_text(html)
    assert "授权单位" in text
    assert "某某公司" in text
```

- [ ] **Step 2: OCR test with monkeypatch**

```python
def test_ocr_page_used_when_native_text_sparse(monkeypatch, tmp_path):
    from app.services.parse import ocr as ocr_mod

    monkeypatch.setattr(ocr_mod, "ocr_image", lambda img: "扫描件授权证书正文")
    # build a minimal PDF page image path or call ocr_mod.ocr_pdf_page(...)
    text = ocr_mod.maybe_ocr_page_text(native_text=" ", page_image_path=tmp_path / "p.png")
    assert "授权证书" in text
```

预先在 tmp_path 写一张小 PNG（Pillow）。

- [ ] **Step 3: Implement**

`ocr.py`:

```python
def page_needs_ocr(native_text: str, min_chars: int) -> bool:
    return len((native_text or "").strip()) < min_chars


def ocr_image(path: Path) -> str:
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(path), lang="chi_sim+eng")
```

在 `convert_pdf_to_markdown` 中：每页提取文本后若 `page_needs_ocr`，渲染 pixmap → 临时图 → OCR → 追加到 markdown，并在该页块标记（可用 HTML comment `<!-- source:ocr -->`）。失败则 `warnings.append(...)`，不抛致命错。

`table_text.py`：解析 `table/{file_id}/*.html`，在 index 阶段把文本 append 到对应占位附近的 fine/large（按 markdown 中 `<!-- table:tbl_NNN -->` 定位所属节点；若定位失败，仍作为独立 fine，`source=table`）。

- [ ] **Step 4: 安装系统依赖说明写入 README 一小段（tesseract 二进制）**——仅 README 必要说明，不写无关文档。

- [ ] **Step 5: PASS → Commit**

```bash
git commit -m "feat: OCR sparse PDF pages and index table text"
```

---

### Task 10: checklist 字段、调度器接线、部分可查状态

**Files:**
- Modify: `backend/app/models.py`（`ChecklistItem.content_source`, `content_target` JSON）
- Modify: `backend/app/services/checklist_validate.py` / mock agent 输出（默认 `precise_search` 或按标题启发式仅用于 Mock）
- Modify: `backend/app/services/scheduler.py`（`RETRIEVAL_PROVIDER=workspace` 时用 `WorkspaceRetrievalProvider`）
- Modify: `backend/app/services/index_scheduler.py`（多 stage：`partial` until vectors+wiki done）
- Test: update `test_scheduler.py`, `test_batch_diagnosis.py`, `test_checklist_*` as needed

- [ ] **Step 1: Schema fields**

```python
# ChecklistItem
content_source: Mapped[str] = mapped_column(String(32), nullable=False, default="precise_search")
content_target: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
```

Mock checklist agent：为演示项填充示例：

- 标题含「全文」且要求招标 → `full_document` + `file_role=tender`
- 标题含「授权/资质」→ `collection` + tags
- 标题含「标书全文」→ `large_segments` + `file_role=bid`
- 其他 → `precise_search`

- [ ] **Step 2: Scheduler wiring**

```python
from app.config import RETRIEVAL_PROVIDER
from app.engine.retrieval_mock import MockRetrievalProvider
from app.engine.retrieval_workspace import WorkspaceRetrievalProvider

def build_retrieval_provider():
    if RETRIEVAL_PROVIDER == "workspace":
        return WorkspaceRetrievalProvider()
    return MockRetrievalProvider()
```

- [ ] **Step 3: Partial search status**

`RetrievalResult.index_status` from latest `IndexJob` for task：

- any `ready` and none failed unfinished → if any `partial|running` then `partial` + `incomplete=True`
- none ready → `unavailable`

Add test：索引仅 segments 完成时 `collection` 仍能返回已 enrich 的块（若 enrich 在 partial 前，按实现调整）。

- [ ] **Step 4: Full pytest**

Run: `cd backend && ../.venv/bin/python -m pytest -q`  
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: wire workspace retrieval into diagnosis and checklist fields"
```

---

### Task 11: Agent OS 适配器骨架（可选接线）

**Files:**
- Create: `backend/app/services/retrieval/enricher_agent_os.py`（若 `app.services.agent_os` 已存在则调用；否则 skip 并用 import 保护）
- Same pattern for `rewrite_agent_os.py`, `rerank_agent_os.py`, `wiki_agent_os.py`
- Modify: factory in enricher/rewrite/rerank/wiki `get_*()` 读取 config
- Test: monkeypatch HTTP，断言 payload 字段

若 Agent OS 客户端尚未合并：本 Task **只提交 Protocol 工厂 + Mock 默认**，并在模块 docstring 注明依赖 `2026-07-17-agent-os-tender-interpretation` 计划落地后将 `AGENT_*=agent_os` 打开。

```python
def get_chunk_enricher():
    if AGENT_CHUNK_ENRICHER == "agent_os":
        from app.services.retrieval.enricher_agent_os import AgentOSChunkEnricher
        return AgentOSChunkEnricher()
    return MockChunkEnricher()
```

- [ ] **Commit**

```bash
git commit -m "feat: agent_os factories for retrieval AI steps"
```

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|-----------|------|
| 双粒度 fine/large + 父章展开 large | 1, 5 |
| knowledge_chunks / tags / wiki / index jobs | 2, 3, 8 |
| 受控词表打标 | 4 |
| full_document / collection / large_segments | 5 |
| precise_search 三路 + BM25 + AI 重排 + 降级 | 6–8 |
| 本地 SQLite/FTS/向量 | 2, 6, 7 |
| OCR 必须 | 9 |
| 表格可检索 | 9 |
| 部分可查 incomplete | 10 |
| RetrievalProvider 替换 Mock | 10 |
| checklist content_source 显式声明 | 10 |
| Agent OS 调用形态 | 11（Mock 默认） |
| 不做 UI / 外部 ES | 无 Task（刻意排除） |

---

## Self-Review Notes

- 类型名：`RetrievalResult` / `RetrievalHit` / `ContentSource` / `SegmentDraft` 在 Task 1/5 定义，后续 Task 不得改名。
- `retrieve_for_category` 与 typed `retrieve` 并存，避免一次改爆 batch diagnosis。
- Embedding 生产模型路径通过配置注入；测试使用 `HashEmbeddingModel`，避免 CI 下载大模型。
- OCR 依赖本机 `tesseract`；测试 monkeypatch `ocr_image`。
- 无 TBD 步骤；Wiki 权威性：collection 以标签为准（Task 5），Wiki 仅 precise 一路（Task 8）。
