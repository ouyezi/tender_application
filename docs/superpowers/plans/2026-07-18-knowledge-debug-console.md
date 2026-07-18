# 知识检索调试台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为工作区提供独立调试页 `/workspaces/:taskId/knowledge`，支持知识块浏览/搜索、Wiki 与索引状态查看，以及对四类 `content_source` 的检索试跑与 `precise_search` 过程深钻。

**Architecture:** 生产 `RetrievalProvider.retrieve` / `RetrievalResult` 保持不变。新增只读 browse 服务与独立 `DebugRetrievalService`（采集 rewrite / 三路召回 / merged / 重排前后 / expansions；AI 失败时 debug 路径置 `degraded=true` 并仍返回合并结果）。HTTP 挂在 `/api/workspaces/{task_id}/knowledge/...`；前端新建页面 + 四 Tab，复用现有工作区样式。

**Tech Stack:** FastAPI、SQLAlchemy async、现有 retrieval（FTS5 / 向量 / Wiki）、pytest；React 18、Vite、react-router、fetch。

**Spec:** `docs/superpowers/specs/2026-07-18-knowledge-debug-console-design.md`

---

## File Structure

```text
backend/app/services/retrieval/debug_types.py   # NEW: DebugRetrievalResult / Trace dataclasses + to_dict
backend/app/services/retrieval/debug.py         # NEW: DebugRetrievalService.retrieve_debug
backend/app/services/retrieval/browse.py        # NEW: list/get chunks, tags, wiki, index-status
backend/app/api/knowledge.py                    # NEW: /api/workspaces/{task_id}/knowledge/*
backend/app/main.py                             # MOD: include knowledge router
backend/app/services/retrieval/provider.py      # MOD (minimal): export task_index_status alias if needed
backend/tests/test_knowledge_browse.py          # NEW
backend/tests/test_knowledge_debug_retrieve.py  # NEW
backend/tests/test_knowledge_api.py             # NEW

frontend/src/api.js                             # MOD: knowledge API helpers
frontend/src/App.jsx                            # MOD: route
frontend/src/pages/KnowledgeDebugPage.jsx       # NEW: page shell + tabs
frontend/src/components/knowledge/              # NEW: tab panels
  ChunksTab.jsx
  RetrieveTab.jsx
  WikiTab.jsx
  IndexStatusTab.jsx
  ChunkDetailDrawer.jsx
frontend/src/pages/WorkspaceDetailPage.jsx      # MOD: link + optional deep-link query
frontend/src/App.css                            # MOD: knowledge debug layout
```

**注意：** 现有 `GET /api/workspaces/{task_id}/index` 返回的是 `index.md` 产物，**不是**知识索引状态。新接口必须用 `.../knowledge/index-status`，勿复用该路径。

---

### Task 1: Debug 类型与 `precise_search` 轨迹

**Files:**
- Create: `backend/app/services/retrieval/debug_types.py`
- Create: `backend/app/services/retrieval/debug.py`
- Create: `backend/tests/test_knowledge_debug_retrieve.py`
- Modify: `backend/tests/stubs/retrieval_ai.py`（确保 stub 也被 debug 模块的 factory 路径 patch 到；见 Step 3）

- [ ] **Step 1: 写失败测试（precise trace + degraded）**

创建 `backend/tests/test_knowledge_debug_retrieve.py`，复用 `test_retrieval_precise.py` 的 `db_session` / `_write_parsed_file` / `_index_file` 模式（可把 helper 复制进本文件，避免跨测试私有依赖）：

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db import init_db_on_connection
from app.models import DiagnosisTask, WorkspaceFile
from app.services import artifact, index_scheduler
from app.services.parse.chunk import chunk_from_tree
from app.services.parse.tree import build_document_tree
from app.services.retrieval.debug import retrieve_debug
from tests.stubs.retrieval_ai import apply_retrieval_ai_stubs


@pytest_asyncio.fixture
async def db_session(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/debug.db", poolclass=NullPool
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(init_db_on_connection)
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.config.UPLOAD_DIR", upload_dir)
    monkeypatch.setattr("app.services.artifact.UPLOAD_DIR", upload_dir)
    apply_retrieval_ai_stubs(monkeypatch)
    # CRITICAL: also patch factories as imported by debug.py
    monkeypatch.setattr(
        "app.services.retrieval.debug.get_query_rewriter",
        lambda: __import__(
            "tests.stubs.retrieval_ai", fromlist=["StubQueryRewriter"]
        ).StubQueryRewriter(),
    )
    monkeypatch.setattr(
        "app.services.retrieval.debug.get_ai_reranker",
        lambda: __import__(
            "tests.stubs.retrieval_ai", fromlist=["StubAiReranker"]
        ).StubAiReranker(),
    )
    await index_scheduler.reset_for_tests()
    async with session_factory() as session:
        yield session
    await index_scheduler.reset_for_tests()
    await engine.dispose()


# Copy _write_parsed_file / _index_file from test_retrieval_precise.py (same body)


@pytest_asyncio.fixture
async def indexed_semantic_task(db_session):
    task_id = "T-DBG-PREC"
    md_text = (
        "# 售后服务\n\n本公司提供完整售后服务与质保支持。\n\n"
        "## 退款政策\n\n本商品支持七天无理由退货。\n\n"
        "## 质保说明\n\n产品质保期为一年。\n"
    )
    wf = await _write_parsed_file(
        db_session,
        task_id=task_id,
        file_id="fdbg001",
        label="售后政策文件",
        md_text=md_text,
    )
    await _index_file(wf)
    return task_id


@pytest.mark.asyncio
async def test_debug_precise_search_includes_trace(db_session, indexed_semantic_task):
    result = await retrieve_debug(
        db_session,
        task_id=indexed_semantic_task,
        content_source="precise_search",
        content_target={"query": "是否支持七天无理由退货"},
        item_hints={"retrieval_hints": ["售后", "退款政策"]},
    )
    assert result.mode == "precise_search"
    assert result.error is None
    assert result.items
    assert result.trace is not None
    assert result.trace.rewrite["vector_query"]
    assert result.trace.rewrite["keywords"]
    assert result.trace.rewrite["wiki_query"]
    assert result.trace.channels["vector"] or result.trace.channels["keyword"] or result.trace.channels["wiki"]
    assert result.trace.merged
    assert result.trace.pre_rerank_order
    assert result.trace.post_rerank_order
    assert result.trace.ai_rerank["used"] is True
    assert "scores_or_ranks" in result.trace.ai_rerank
    # final items are large after parent expansion
    assert all(item.segment_level == "large" for item in result.items)


@pytest.mark.asyncio
async def test_debug_precise_search_degrades_when_reranker_raises(
    db_session, indexed_semantic_task, monkeypatch
):
    class Boom:
        async def rerank(self, requirement, hits):
            raise RuntimeError("rerank down")

    monkeypatch.setattr(
        "app.services.retrieval.debug.get_ai_reranker", lambda: Boom()
    )
    result = await retrieve_debug(
        db_session,
        task_id=indexed_semantic_task,
        content_source="precise_search",
        content_target={"query": "是否支持七天无理由退货"},
    )
    assert result.degraded is True
    assert result.trace.ai_rerank["used"] is False
    assert "rerank" in (result.trace.ai_rerank.get("degraded_reason") or "").lower() or \
        "rerank" in (result.error or "").lower() or \
        result.trace.ai_rerank.get("degraded_reason")
    assert result.items  # still returns merged ranking
```

- [ ] **Step 2: Run 测试确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_knowledge_debug_retrieve.py::test_debug_precise_search_includes_trace -v`

Expected: FAIL（`retrieve_debug` 未定义）

- [ ] **Step 3: 实现 `debug_types.py` + `debug.py`（precise 路径）**

`debug_types.py`：

```python
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.engine.base import RetrievalHit


@dataclass
class DebugTrace:
    rewrite: dict[str, Any] = field(default_factory=dict)
    channels: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    merged: list[dict[str, Any]] = field(default_factory=list)
    pre_rerank_order: list[str] = field(default_factory=list)
    post_rerank_order: list[str] = field(default_factory=list)
    ai_rerank: dict[str, Any] = field(default_factory=dict)
    expansions: list[dict[str, str]] = field(default_factory=list)
    skipped_stages: list[str] = field(default_factory=list)


@dataclass
class DebugRetrievalResult:
    mode: str
    items: list[RetrievalHit]
    index_status: str
    incomplete: bool = False
    degraded: bool = False
    error: str | None = None
    path_note: str = ""
    trace: DebugTrace | None = None

    def to_dict(self) -> dict[str, Any]:
        def hit_dict(h: RetrievalHit) -> dict[str, Any]:
            return {
                "chunk_id": h.chunk_id,
                "file_id": h.file_id,
                "node_id": h.node_id,
                "segment_level": h.segment_level,
                "title": h.title,
                "summary": h.summary,
                "title_path": h.title_path,
                "tags": h.tags,
                "text": h.text,
                "child_chunk_ids": h.child_chunk_ids,
                "score": h.score,
            }

        return {
            "mode": self.mode,
            "items": [hit_dict(i) for i in self.items],
            "index_status": self.index_status,
            "incomplete": self.incomplete,
            "degraded": self.degraded,
            "error": self.error,
            "path_note": self.path_note,
            "trace": asdict(self.trace) if self.trace else None,
        }
```

`debug.py` 核心要求：

1. `async def retrieve_debug(session, *, task_id, content_source, content_target, item_hints=None) -> DebugRetrievalResult`
2. 复用 `provider._task_index_status`、`provider._search_vector_channel`、`provider._merge_channel_scores`、`provider._chunk_to_hit`、以及 fine→large 展开逻辑（可抽一小段私有函数 `_expand_fine_hits`，或从 provider 复制展开循环并记录 `expansions`）。
3. **不要**调用生产 `retrieve()` 作为 precise 主路径（否则拿不到 trace）；允许对 `full_document` / `collection` / `large_segments` 在 Task 2 中委托 `provider.retrieve` 再包装。
4. precise 流程：rewrite（try/except → degraded）→ 三路召回 → merge → 填 `channel_flags` → pre_rerank_order → AI rerank（try/except → degraded，失败时 post=pre）→ expand → items。
5. `ai_rerank.scores_or_ranks`：按 `post_rerank_order` 生成 `[{chunk_id, rank: 1-based}]`；`rationale` 若 reranker 无此字段则为 `None`。
6. `channels.keyword` 条目带 FTS `score`；vector/wiki 带各自 score。
7. 更新 `apply_retrieval_ai_stubs` 末尾，增加对 `app.services.retrieval.debug.get_query_rewriter` / `get_ai_reranker` 的 patch（与 provider 一致）。

- [ ] **Step 4: Run 测试通过**

Run: `.venv/bin/python -m pytest backend/tests/test_knowledge_debug_retrieve.py -v`

Expected: PASS（本 Task 两个用例）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/debug_types.py \
  backend/app/services/retrieval/debug.py \
  backend/tests/test_knowledge_debug_retrieve.py \
  backend/tests/stubs/retrieval_ai.py
git commit -m "$(cat <<'EOF'
feat: add debug retrieval trace for precise_search

Introduce DebugRetrievalResult with channel/rerank traces and degrade
gracefully on AI failures without changing production retrieve.
EOF
)"
```

---

### Task 2: Debug 其余三种 mode + 配置错误

**Files:**
- Modify: `backend/app/services/retrieval/debug.py`
- Modify: `backend/tests/test_knowledge_debug_retrieve.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_debug_collection_path_note_no_trace_channels(
    db_session, indexed_semantic_task
):
    # indexed fixture may or may not have 退款政策 tag; use a known seeded tag after enrich
    result = await retrieve_debug(
        db_session,
        task_id=indexed_semantic_task,
        content_source="collection",
        content_target={"target_tags": ["退款政策"]},
    )
    assert result.mode == "collection"
    assert "三路" in result.path_note or "precise_search" in result.path_note.lower() or \
        "未走" in result.path_note
    assert result.trace is None or result.trace.skipped_stages


@pytest.mark.asyncio
async def test_debug_missing_query_is_config_error(db_session, indexed_semantic_task):
    with pytest.raises(DebugConfigError) as ei:
        await retrieve_debug(
            db_session,
            task_id=indexed_semantic_task,
            content_source="precise_search",
            content_target={},
        )
    assert "query" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_debug_invalid_tag_is_config_error(db_session, indexed_semantic_task):
    with pytest.raises(DebugConfigError) as ei:
        await retrieve_debug(
            db_session,
            task_id=indexed_semantic_task,
            content_source="collection",
            content_target={"target_tags": ["不是合法标签xyz"]},
        )
    assert ei.value.allowed_tags  # list of legal names
```

定义：

```python
class DebugConfigError(Exception):
    def __init__(self, message: str, *, allowed_tags: list[str] | None = None):
        super().__init__(message)
        self.allowed_tags = allowed_tags or []
```

- [ ] **Step 2: Run 确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_knowledge_debug_retrieve.py::test_debug_missing_query_is_config_error -v`

Expected: FAIL

- [ ] **Step 3: 实现包装**

对 `full_document` / `collection` / `large_segments`：

1. 校验必填字段；非法标签 → `DebugConfigError`（附 `load_tag_catalog` 合法名）。
2. 调用 `from app.services.retrieval.provider import retrieve as provider_retrieve`，把结果映射为 `DebugRetrievalResult`（items/mode/index_status/incomplete/error）。
3. `path_note` 固定中文说明，例如：`collection：按受控标签过滤，未走查询重写与三路召回。`
4. `trace=DebugTrace(skipped_stages=["rewrite","vector","keyword","wiki","ai_rerank"])` 或 `trace=None`（与测试断言一致，二选一并固定）。
5. `unknown content_source` / 空 `content_source` → `DebugConfigError`。

- [ ] **Step 4: 测试通过并 commit**

```bash
git add backend/app/services/retrieval/debug.py backend/tests/test_knowledge_debug_retrieve.py
git commit -m "feat: wrap typed retrieval modes in debug retrieve with path notes"
```

---

### Task 3: 知识块浏览服务

**Files:**
- Create: `backend/app/services/retrieval/browse.py`
- Create: `backend/tests/test_knowledge_browse.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_list_chunks_filters_segment_level(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_chunks

    page = await list_chunks(
        db_session,
        task_id=indexed_semantic_task,
        segment_level="fine",
        page=1,
        page_size=50,
    )
    assert page["total"] >= 1
    assert all(c["segment_level"] == "fine" for c in page["items"])
    assert "title" in page["items"][0]
    assert "search_degraded" in page  # bool


@pytest.mark.asyncio
async def test_get_chunk_detail_includes_text(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_chunks, get_chunk

    page = await list_chunks(db_session, task_id=indexed_semantic_task, page=1, page_size=5)
    chunk_id = page["items"][0]["chunk_id"]
    detail = await get_chunk(db_session, task_id=indexed_semantic_task, chunk_id=chunk_id)
    assert detail["chunk_id"] == chunk_id
    assert "text" in detail
    assert "tags" in detail


@pytest.mark.asyncio
async def test_list_chunks_q_matches_title_or_body(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_chunks

    page = await list_chunks(
        db_session, task_id=indexed_semantic_task, q="无理由", page=1, page_size=20
    )
    assert page["total"] >= 1
```

- [ ] **Step 2: Run 确认失败**

- [ ] **Step 3: 实现 `browse.py`**

```python
async def list_chunks(
    session,
    *,
    task_id: str,
    q: str | None = None,
    file_id: str | None = None,
    segment_level: str | None = None,
    tag: str | None = None,
    source: str | None = None,
    index_status: str | None = None,
    embedding_status: str | None = None,
    node_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict: ...

async def get_chunk(session, *, task_id: str, chunk_id: str) -> dict | None: ...

async def list_tags(session) -> list[dict]: ...  # enabled controlled tags

TEXT_PREVIEW_LIMIT = 4000  # detail truncation
```

实现要点：

- 列表默认**不**返回全文，只返回 `summary` 与短预览可选；详情用 `load_chunk_text`，超限截断并设 `text_truncated=True`。
- `q`：优先 `search_fts(session, task_id, q, limit=...)` 得到 chunk_id 集合再过滤；若 FTS 抛错或不可用 → `search_degraded=True`，改用 SQL `OR` 匹配 `title`/`summary`/`description`（`LIKE %q%`）。
- `node_id`：过滤 `node_id == X OR ancestor_node_ids JSON 包含 X`（可用 Python 侧过滤或 `LIKE '%"X"%'` 谨慎匹配）。
- `tag`：Python 侧解析 tags JSON 过滤（与 collection 一致看 name）。
- 分页：`page` 从 1 起；返回 `{items, total, page, page_size, search_degraded}`。
- 任务不存在：由 API 层 404；browse 可返回空列表。

- [ ] **Step 4: 测试通过并 commit**

```bash
git commit -m "feat: add knowledge chunk browse list/detail helpers"
```

---

### Task 4: Wiki 与索引状态服务

**Files:**
- Modify: `backend/app/services/retrieval/browse.py`
- Modify: `backend/tests/test_knowledge_browse.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_list_wiki_pages(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import list_wiki_pages

    pages = await list_wiki_pages(db_session, task_id=indexed_semantic_task)
    assert isinstance(pages, list)
    # stub wiki builder creates pages for tagged fines
    assert pages  # after enrich+wiki on indexed fixture


@pytest.mark.asyncio
async def test_index_status_summary(db_session, indexed_semantic_task):
    from app.services.retrieval.browse import get_index_status

    status = await get_index_status(db_session, task_id=indexed_semantic_task)
    assert status["index_status"] in ("ready", "partial", "unavailable")
    assert "incomplete" in status
    assert status["counts"]["fine"] >= 1
    assert status["counts"]["large"] >= 1
    assert status["files"]
    assert "status" in status["files"][0]
    assert "stage" in status["files"][0]
```

- [ ] **Step 2–4: 实现并提交**

`get_index_status`：

- 复用 `provider._task_index_status`
- 聚合 `KnowledgeChunk`：fine/large 计数、`embedding_status==ready` 比例
- `files`：该 task 下所有 `IndexJob` + 可选 join `WorkspaceFile.label`
- `fts_available`: 尝试一次空/简单 FTS 或检查 FTS 表存在（简单：`True` 若 task 有 ready fine；实现可用 try `search_fts(..., limit=1)`）

```bash
git commit -m "feat: add wiki browse and knowledge index-status summary"
```

---

### Task 5: HTTP API

**Files:**
- Create: `backend/app/api/knowledge.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_knowledge_api.py`

- [ ] **Step 1: 写 API 测试**

使用 `conftest.client` 时注意：它用 `Base.metadata.create_all`，**可能没有 FTS**。更稳妥：本文件自建与 Task 1 相同的 `db_session` + 手动挂 `AsyncClient`，或在测试里直接测路由 handler；推荐 **httpx ASGI + init_db_on_connection** 复制 client 片段：

```python
@pytest.mark.asyncio
async def test_api_chunks_and_debug_retrieve(api_client, indexed_task_id):
    r = await api_client.get(f"/api/workspaces/{indexed_task_id}/knowledge/chunks")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body

    r = await api_client.get(f"/api/workspaces/{indexed_task_id}/knowledge/index-status")
    assert r.status_code == 200
    assert r.json()["index_status"] in ("ready", "partial", "unavailable")

    r = await api_client.post(
        f"/api/workspaces/{indexed_task_id}/knowledge/debug/retrieve",
        json={
            "content_source": "precise_search",
            "content_target": {"query": "七天无理由"},
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "precise_search"
    assert data["trace"]["rewrite"]

    r = await api_client.post(
        f"/api/workspaces/{indexed_task_id}/knowledge/debug/retrieve",
        json={
            "content_source": "collection",
            "content_target": {"target_tags": ["不是合法标签xyz"]},
        },
    )
    assert r.status_code == 400
    assert "allowed" in r.json().get("detail", "").lower() or \
        isinstance(r.json().get("detail"), dict)


@pytest.mark.asyncio
async def test_api_task_not_found(api_client):
    r = await api_client.get("/api/workspaces/T-missing/knowledge/chunks")
    assert r.status_code == 404
```

- [ ] **Step 2: 实现 router**

```python
# backend/app/api/knowledge.py
router = APIRouter(
    prefix="/api/workspaces/{task_id}/knowledge",
    tags=["knowledge"],
)

@router.get("/chunks")
@router.get("/chunks/{chunk_id}")
@router.get("/tags")
@router.get("/wiki")
@router.get("/wiki/{wiki_id}")
@router.get("/index-status")
@router.post("/debug/retrieve")
```

每个 handler：先 `db.get(DiagnosisTask, task_id)` → 404；再调 browse/debug。  
`DebugConfigError` → `HTTPException(400, detail={"message": str(e), "allowed_tags": e.allowed_tags})`。  
`POST /debug/retrieve` body：

```python
class DebugRetrieveIn(BaseModel):
    content_source: str
    content_target: dict[str, Any] = {}
    item_hints: dict[str, Any] | None = None
```

返回 `result.to_dict()`。

`main.py`：

```python
from app.api.knowledge import router as knowledge_router
app.include_router(knowledge_router)
```

- [ ] **Step 3: 测试通过并 commit**

```bash
git commit -m "feat: expose knowledge browse and debug retrieve HTTP APIs"
```

---

### Task 6: 前端壳 — 路由、API、页头、Tab 框架

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/App.jsx`
- Create: `frontend/src/pages/KnowledgeDebugPage.jsx`
- Modify: `frontend/src/pages/WorkspaceDetailPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: API helpers**

```js
export function getKnowledgeIndexStatus(taskId) {
  return request(`/api/workspaces/${taskId}/knowledge/index-status`)
}
export function getKnowledgeChunks(taskId, params = {}) {
  const qs = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  })
  const q = qs.toString()
  return request(`/api/workspaces/${taskId}/knowledge/chunks${q ? `?${q}` : ''}`)
}
export function getKnowledgeChunk(taskId, chunkId) {
  return request(`/api/workspaces/${taskId}/knowledge/chunks/${encodeURIComponent(chunkId)}`)
}
export function getKnowledgeTags(taskId) {
  return request(`/api/workspaces/${taskId}/knowledge/tags`)
}
export function getKnowledgeWiki(taskId) {
  return request(`/api/workspaces/${taskId}/knowledge/wiki`)
}
export function getKnowledgeWikiPage(taskId, wikiId) {
  return request(`/api/workspaces/${taskId}/knowledge/wiki/${wikiId}`)
}
export function debugKnowledgeRetrieve(taskId, body) {
  return request(`/api/workspaces/${taskId}/knowledge/debug/retrieve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}
```

- [ ] **Step 2: 路由 + 入口链接**

`App.jsx`：

```jsx
import KnowledgeDebugPage from './pages/KnowledgeDebugPage'
// ...
<Route path="/workspaces/:taskId/knowledge" element={<KnowledgeDebugPage />} />
```

`WorkspaceDetailPage.jsx` 在「诊断详情」旁：

```jsx
<Link className="btn btn-secondary" to={`/workspaces/${taskId}/knowledge`}>
  知识检索
</Link>
```

- [ ] **Step 3: `KnowledgeDebugPage.jsx` 壳**

- `useParams().taskId`；`useSearchParams` 读 `tab`（默认 `chunks`）
- 挂载拉 `getKnowledgeIndexStatus`；页头显示 status / counts / incomplete
- Tab 按钮：`chunks | retrieve | wiki | index`，点击写 URL
- 四个占位面板：`{tab === 'chunks' && <ChunksTab .../>}` 等（组件可先 inline 返回 `<p>知识块</p>`）
- 样式：`.knowledge-debug-page`、`.knowledge-tabs`、`.knowledge-tab` active 态，写入 `App.css`，复用 `.page` / `.btn` / `.detail-section`

- [ ] **Step 4: 手动冒烟**（dev server）打开工作区 → 点「知识检索」→ 见页头与 Tab

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: add knowledge debug page shell and workspace entry link"
```

---

### Task 7: 知识块 Tab + 详情抽屉

**Files:**
- Create: `frontend/src/components/knowledge/ChunksTab.jsx`
- Create: `frontend/src/components/knowledge/ChunkDetailDrawer.jsx`
- Modify: `frontend/src/pages/KnowledgeDebugPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 实现 ChunksTab**

功能清单（全部落地，勿留 stub）：

1. 搜索框（300ms 防抖）→ `getKnowledgeChunks(taskId, { q, ...filters, page })`
2. 筛选：`segment_level` select、`tag`（从 `getKnowledgeTags`）、`source`、`index_status`、`file_id`（若页头/父组件传入 files 列表；否则先省略 file 下拉，仅保留其它筛选）
3. 「按章节树筛选」checkbox：选中后出现文件 select + 复用 `getWorkspaceTree`（已有 `frontend/src/api.js`）；点节点设 `node_id`
4. 表格列：level、title、path 末两级、tags、source、index/embedding status
5. 点击行 → 打开 `ChunkDetailDrawer`（`getKnowledgeChunk`）
6. 若 `search_degraded`：角标「搜索已降级（非 FTS）」
7. 分页：上一页/下一页
8. URL：`chunk_id` 存在时自动打开详情

`ChunkDetailDrawer`：展示 title_path、summary、description、tags+confidence、child_chunk_ids（点击切换 chunk）、text、链接：

```jsx
<Link to={`/workspaces/${taskId}?file_id=${fileId}&node_id=${nodeId}`}>
  打开阅读器
</Link>
```

- [ ] **Step 2: WorkspaceDetailPage 深链（最小）**

读取 `useSearchParams` 的 `file_id` / `node_id`：若存在，在 `workspace` 加载后自动 `setSelectedFile` 为对应文件并触发现有 tree/content 加载逻辑（在现有 `useEffect` 上扩展，勿重写整页）。

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: browse and filter knowledge chunks in debug console"
```

---

### Task 8: 检索试跑 Tab（深钻 UI）

**Files:**
- Create: `frontend/src/components/knowledge/RetrieveTab.jsx`
- Modify: `frontend/src/pages/KnowledgeDebugPage.jsx`
- Modify: `frontend/src/App.css`

- [ ] **Step 1: 实现表单**

- `content_source` radio/select：四个值
- 动态字段：
  - `full_document`: `file_role` select `tender|bid`
  - `collection`: tags 多选（checkbox 列表 from tags API）
  - `large_segments`: `file_role` + optional `root_node_id` text input
  - `precise_search`: `query` textarea + optional hints（逗号分隔 → `item_hints.retrieval_hints`）
- 按钮「运行」→ `debugKnowledgeRetrieve`；loading / error 状态

- [ ] **Step 2: 结果区**

1. 状态条：mode、index_status、incomplete、degraded、error
2. `path_note` 段落
3. 最终命中列表（score、level、title、title_path）；「在知识块中打开」→ `setSearchParams({ tab: 'chunks', chunk_id })`
4. 若 `trace`：折叠 `<details>` 分区
   - 查询重写 JSON
   - 三路召回表（标记同时出现在多路的 chunk）
   - merged（score + channel_flags）
   - pre vs post 顺序（两列或并列表）
   - AI ranks / degraded_reason / rationale
   - expansions
5. 「复制 JSON」：`navigator.clipboard.writeText(JSON.stringify({request, response}, null, 2))`

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add retrieval trial tab with deep-drill trace panels"
```

---

### Task 9: Wiki Tab + 索引状态 Tab

**Files:**
- Create: `frontend/src/components/knowledge/WikiTab.jsx`
- Create: `frontend/src/components/knowledge/IndexStatusTab.jsx`
- Modify: `frontend/src/pages/KnowledgeDebugPage.jsx`

- [ ] **Step 1: WikiTab**

- `getKnowledgeWiki` 列表；点击 `getKnowledgeWikiPage`
- 详情：summary、description、tags、member 卡片（标题；点击跳转 chunks tab + chunk_id）
- 页内提示文案：`权威召回以标签过滤为准；请在「检索试跑」用 collection 对照成员列表。`

- [ ] **Step 2: IndexStatusTab**

- 使用页头已拉的 status，或 Tab 激活时再拉一次
- 汇总数字 + 文件表（status/stage/progress/error/时间）
- `partial`/`failed` 行加 class 高亮
- 文件名链到 `/workspaces/${taskId}`（可选带 file_id）

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add wiki and index-status tabs to knowledge debug console"
```

---

### Task 10: 端到端验收与回归

**Files:** 无新文件；修缺陷

- [ ] **Step 1: 后端全量相关测试**

Run:

```bash
.venv/bin/python -m pytest \
  backend/tests/test_knowledge_debug_retrieve.py \
  backend/tests/test_knowledge_browse.py \
  backend/tests/test_knowledge_api.py \
  backend/tests/test_retrieval_precise.py \
  backend/tests/test_retrieval_provider_modes.py \
  -v
```

Expected: 全部 PASS（生产 retrieve 行为不变，含 AI 失败仍抛错的旧测试）。

- [ ] **Step 2: 手动验收清单（对照规格）**

1. 工作区 → 知识检索 → 四 Tab 可切换，URL `tab=` 可刷新保持  
2. 知识块：搜索「无理由」、筛选 fine、打开详情见正文与标签  
3. 试跑 precise_search：见 rewrite、三路、merged、重排顺序  
4. 试跑非法 collection 标签：前端展示 400 信息  
5. 索引状态与页头 summary 一致  
6. 生产诊断路径未改（可选跑一个已有任务）

- [ ] **Step 3: 若有修复则 commit**

```bash
git commit -m "fix: polish knowledge debug console after acceptance checks"
```

---

## Spec Coverage Checklist

| 规格要求 | Task |
|----------|------|
| 独立路由 `/workspaces/:taskId/knowledge` | 6 |
| 四 Tab：块 / 试跑 / Wiki / 索引 | 6–9 |
| 块浏览筛选搜索 + 可选章节树 | 3, 7 |
| 详情标签置信度 / child / 正文 | 3, 7 |
| 四类 content_source 试跑 | 2, 5, 8 |
| precise 深钻（rewrite/三路/merged/重排/分数/理由） | 1, 8 |
| AI 失败 degraded 仍有结果（仅 debug） | 1 |
| 生产 RetrievalResult 不变 | 1–2（不改 provider 成功/失败语义） |
| Wiki 只读 + 人工对照提示 | 4, 9 |
| index-status 汇总 | 4, 5, 9 |
| 配置错误 400 / 检索空 200 | 2, 5 |
| 工作区入口链接 | 6 |
| 阅读器深链 | 7 |
| task_id 隔离 | 3, 5 |

---

## Self-Review Notes

- **Placeholder scan:** 无 TBD；AI `rationale` 允许为 `null`（stub 仅返回顺序），`scores_or_ranks` 必填。
- **degraded 语义分裂：** 生产 `retrieve` 遇 AI 错误仍抛异常；仅 `retrieve_debug` 捕获。Task 10 回归旧测试防回归。
- **与 `/index` 路径冲突：** 新 API 使用 `/knowledge/index-status`。
- **conftest FTS：** API 测试自建 `init_db_on_connection`，勿依赖默认 `client` fixture 的 `create_all`。
