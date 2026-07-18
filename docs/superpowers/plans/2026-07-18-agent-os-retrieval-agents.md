# Agent OS 文档检索智能体接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布四个检索 AI 应用，将索引富化 / Wiki 文案 / 查询重写 / AI 重排从 Mock 切到 `AgentOSClient.invoke_app`，精确查找失败硬失败，并在 README 强制复用客户端。

**Architecture:** 复用已有 `AgentOSClient`；四个适配器镜像 `AgentOSChecklistAgent`（可注入 `invoke_app`）；Wiki 由后端按标签聚合成员后再调文案应用；生产删除 Mock 与 `AGENT_*=mock|agent_os` 开关；pytest 默认用 stub / 假 invoke，不打真实 Agent OS。

**Tech Stack:** Python 3 / FastAPI / httpx / pytest / Agent OS `POST /v1/apps/invoke` / `agent-create-publish` skill

**Spec:** `docs/superpowers/specs/2026-07-18-agent-os-retrieval-agents-design.md`

---

## File Structure

| Path | Responsibility |
|---|---|
| `docs/agents_config/retrieval_chunk_enricher.json` | 富化应用契约快照 |
| `docs/agents_config/retrieval_wiki_writer.json` | Wiki 文案应用契约快照 |
| `docs/agents_config/retrieval_query_rewriter.json` | 查询重写应用契约快照 |
| `docs/agents_config/retrieval_ai_reranker.json` | AI 重排应用契约快照 |
| `backend/app/services/retrieval/enricher_agent_os.py` | `AgentOSChunkEnricher` + app 常量 |
| `backend/app/services/retrieval/wiki_agent_os.py` | `AgentOSWikiBuilder`：聚合 → invoke → 落库 |
| `backend/app/services/retrieval/rewrite_agent_os.py` | `AgentOSQueryRewriter` |
| `backend/app/services/retrieval/rerank_agent_os.py` | `AgentOSAiReranker` |
| `backend/app/services/retrieval/enricher.py` | Protocol + factory（默认 Agent OS）；删除 Mock |
| `backend/app/services/retrieval/wiki.py` | Protocol + factory + `search_wiki`；删除 Mock；聚合可抽到 agent_os |
| `backend/app/services/retrieval/rewrite.py` | Protocol + factory；删除 Mock |
| `backend/app/services/retrieval/rerank.py` | Protocol + factory；删除 Mock |
| `backend/app/services/retrieval/provider.py` | 去掉 `degraded` 吞异常路径 |
| `backend/app/config.py` | 删除四个 `AGENT_*` 开关 |
| `backend/tests/stubs/retrieval_ai.py` | 测试用规则 stub（原 Mock 行为） |
| `backend/tests/conftest.py` | 默认把四个 factory 打到 stub |
| `backend/tests/test_retrieval_*_agent_os.py` | 适配器单测 |
| `README.md` | Agent OS 调用规范 |
| `backend/app/services/agent_os.py` | **已存在，勿重造** |

---

### Task 1: 用 agent-create-publish 发布四个应用并落盘契约

**Files:**
- Create: `docs/agents_config/retrieval_chunk_enricher.json`
- Create: `docs/agents_config/retrieval_wiki_writer.json`
- Create: `docs/agents_config/retrieval_query_rewriter.json`
- Create: `docs/agents_config/retrieval_ai_reranker.json`
- Skill: `.cursor/skills/agent-create-publish/SKILL.md`（需 `config.local.json`）

- [ ] **Step 1: 确认 Agent OS 可连且可列模型**

```bash
# 从 .cursor/skills/agent-create-publish/config.local.json 解析 BASE_URL / AUTH
cd /Users/tongqianni/xlab/tender_application
test -f .cursor/skills/agent-create-publish/config.local.json
# 然后按 skill 设置 BASE_URL / AUTH_ARGS，执行:
curl -sS -X POST "$BASE_URL/api/v1/models/list" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{"page":1,"pageSize":100}'
```

Expected: `code === 0`，记下可用 `modelId`（优先与检查项相同的 `mdl_544a969b` / qwen3.7-max；重排若有 flash 则选用）。

- [ ] **Step 2: 按 skill 依次发布四个应用（每轮：草案 → 用户确认 → Steps 1–7）**

对每个应用使用下列草案要点（完整 systemPrompt 在确认草案时写入 skill 模板）：

**A. retrieval_chunk_enricher / retrieval_chunk_enricher_app**

- mode=`api`，`apiConfig.syncType=sync`，`timeoutMs=180000`，`concurrency=10`
- temperature=`0.3`，thinking=`false`，streaming=`false`，multiTurn=`false`
- input: `task_id` (string, req), `catalog_json` (string, req), `segments_json` (string, req)
- output: `segments_json` (string, req)
- systemPrompt 要点：只允许 `catalog_json` 内标签名；为每段生成 title/summary/description/tags[{name,confidence}]；输出仅 `segments_json` 字符串且 chunk_id 与入参一一对应。

**B. retrieval_wiki_writer / retrieval_wiki_writer_app**

- 同上 timeout/runtime
- input: `task_id`, `pages_json`
- output: `pages_json`（每页 title/summary/description，按 tag_name 对齐）
- systemPrompt：根据成员摘要写主题页文案；不要改 member_chunk_ids。

**C. retrieval_query_rewriter / retrieval_query_rewriter_app**

- `timeoutMs=60000`
- input: `query`, `hints_json`
- output: `vector_query`, `keywords_json`, `wiki_query`
- systemPrompt：服务招标诊断精确检索；产出向量句、关键字数组 JSON、Wiki 主题查询。

**D. retrieval_ai_reranker / retrieval_ai_reranker_app**

- `timeoutMs=60000`；若有 flash 模型优先；thinking=`false`；temperature=`0.2`
- input: `requirement`, `hits_json`
- output: `chunk_ids_json`
- systemPrompt：仅依据 title+summary 相对 requirement 排序；返回全部 chunk_id 的降序 JSON 数组，不得编造 id。

每发布成功一个，立刻把响应快照写入对应 `docs/agents_config/<enName>.json`（字段结构对齐 `docs/agents_config/tender_checklist_generator.json`：agent / application / invoke / model / io）。

- [ ] **Step 3: Commit 四份契约**

```bash
git add docs/agents_config/retrieval_chunk_enricher.json \
  docs/agents_config/retrieval_wiki_writer.json \
  docs/agents_config/retrieval_query_rewriter.json \
  docs/agents_config/retrieval_ai_reranker.json
git commit -m "$(cat <<'EOF'
docs: persist four retrieval Agent OS app configs

EOF
)"
```

---

### Task 2: AgentOSQueryRewriter（TDD）

**Files:**
- Modify: `backend/app/services/retrieval/rewrite_agent_os.py`
- Create: `backend/tests/test_retrieval_rewrite_agent_os.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_retrieval_rewrite_agent_os.py`:

```python
import json
import pytest

from app.services.retrieval.rewrite_agent_os import (
    RETRIEVAL_QUERY_REWRITER_APP_NAME,
    AgentOSQueryRewriter,
    QueryRewriteResponseError,
)


@pytest.mark.asyncio
async def test_rewrite_invokes_app_with_json_hints():
    calls = []

    async def fake_invoke(app_name, input_data):
        calls.append((app_name, input_data))
        return {
            "vector_query": "七天无理由退款政策",
            "keywords_json": json.dumps(["退款", "无理由"], ensure_ascii=False),
            "wiki_query": "退款政策",
        }

    rewriter = AgentOSQueryRewriter(invoke_app=fake_invoke)
    out = await rewriter.rewrite("是否支持7天无理由", hints=["售后"])
    assert calls[0][0] == RETRIEVAL_QUERY_REWRITER_APP_NAME
    assert calls[0][1]["query"] == "是否支持7天无理由"
    assert json.loads(calls[0][1]["hints_json"]) == ["售后"]
    assert out["vector_query"] == "七天无理由退款政策"
    assert out["keywords"] == ["退款", "无理由"]
    assert out["wiki_query"] == "退款政策"


@pytest.mark.asyncio
async def test_rewrite_missing_vector_query_raises():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"keywords_json": "[]", "wiki_query": "x"}

    rewriter = AgentOSQueryRewriter(invoke_app=fake_invoke)
    with pytest.raises(QueryRewriteResponseError):
        await rewriter.rewrite("q", hints=[])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_rewrite_agent_os.py -v
```

Expected: FAIL（缺常量 / 缺 `QueryRewriteResponseError` / IO 不匹配）。

- [ ] **Step 3: Implement adapter**

Replace `backend/app/services/retrieval/rewrite_agent_os.py` with:

```python
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from app.services.agent_os import AgentOSClient

RETRIEVAL_QUERY_REWRITER_APP_NAME = "retrieval_query_rewriter_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class QueryRewriteResponseError(ValueError):
    pass


class AgentOSQueryRewriter:
    def __init__(
        self,
        *,
        app_name: str = RETRIEVAL_QUERY_REWRITER_APP_NAME,
        client: Optional[AgentOSClient] = None,
        invoke_app: Optional[InvokeFn] = None,
    ) -> None:
        self.app_name = app_name
        self._client = client
        self._invoke_app = invoke_app

    async def _invoke(self, input_data: dict[str, object]) -> dict[str, object]:
        if self._invoke_app is not None:
            return await self._invoke_app(self.app_name, input_data)
        client = self._client or AgentOSClient()
        return await client.invoke_app(self.app_name, input_data)

    async def rewrite(
        self,
        query: str,
        hints: list[str] | None = None,
    ) -> dict[str, object]:
        payload = await self._invoke(
            {
                "query": query,
                "hints_json": json.dumps(hints or [], ensure_ascii=False),
            }
        )
        vector_query = payload.get("vector_query")
        wiki_query = payload.get("wiki_query")
        if not isinstance(vector_query, str) or not vector_query.strip():
            raise QueryRewriteResponseError("vector_query invalid")
        if not isinstance(wiki_query, str) or not wiki_query.strip():
            raise QueryRewriteResponseError("wiki_query invalid")
        raw_keywords = payload.get("keywords_json")
        if isinstance(raw_keywords, str):
            try:
                keywords = json.loads(raw_keywords)
            except json.JSONDecodeError as exc:
                raise QueryRewriteResponseError("keywords_json invalid") from exc
        elif isinstance(raw_keywords, list):
            keywords = raw_keywords
        else:
            raise QueryRewriteResponseError("keywords_json missing")
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise QueryRewriteResponseError("keywords must be string list")
        return {
            "vector_query": vector_query.strip(),
            "keywords": keywords,
            "wiki_query": wiki_query.strip(),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_rewrite_agent_os.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/retrieval/rewrite_agent_os.py \
  backend/tests/test_retrieval_rewrite_agent_os.py
git commit -m "$(cat <<'EOF'
feat: Agent OS query rewriter adapter with hard-fail parse

EOF
)"
```

---

### Task 3: AgentOSAiReranker（TDD）

**Files:**
- Modify: `backend/app/services/retrieval/rerank_agent_os.py`
- Create: `backend/tests/test_retrieval_rerank_agent_os.py`

- [ ] **Step 1: Write failing tests**

```python
import json
import pytest

from app.engine.base import RetrievalHit
from app.services.retrieval.rerank_agent_os import (
    RETRIEVAL_AI_RERANKER_APP_NAME,
    AgentOSAiReranker,
    AiRerankResponseError,
)


def _hit(chunk_id: str, title: str = "t") -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        file_id="f1",
        node_id="n1",
        segment_level="fine",
        title=title,
        summary="s",
        tags=[],
        title_path=["a"],
        score=0.5,
    )


@pytest.mark.asyncio
async def test_rerank_returns_ordered_ids():
    async def fake_invoke(app_name, input_data):
        assert app_name == RETRIEVAL_AI_RERANKER_APP_NAME
        assert input_data["requirement"] == "退款"
        hits = json.loads(input_data["hits_json"])
        assert [h["chunk_id"] for h in hits] == ["c2", "c1"]
        return {"chunk_ids_json": json.dumps(["c1", "c2"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    out = await reranker.rerank("退款", [_hit("c2"), _hit("c1")])
    assert out == ["c1", "c2"]


@pytest.mark.asyncio
async def test_rerank_unknown_id_raises():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"chunk_ids_json": json.dumps(["c9"])}

    reranker = AgentOSAiReranker(invoke_app=fake_invoke)
    with pytest.raises(AiRerankResponseError):
        await reranker.rerank("q", [_hit("c1")])
```

若 `RetrievalHit` 构造参数与 `engine/base.py` 不一致，以该文件实际字段为准补齐默认值。

- [ ] **Step 2: Run to verify fail**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_rerank_agent_os.py -v
```

- [ ] **Step 3: Implement**

Replace `backend/app/services/retrieval/rerank_agent_os.py`：镜像 Task 2 的注入模式；常量 `RETRIEVAL_AI_RERANKER_APP_NAME = "retrieval_ai_reranker_app"`；请求字段 `requirement` + `hits_json`；解析 `chunk_ids_json`；要求返回列表为入参 id 集合的排列（相同集合、无未知 id），否则 `AiRerankResponseError`。

- [ ] **Step 4: Pass + Commit**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_rerank_agent_os.py -v
git add backend/app/services/retrieval/rerank_agent_os.py \
  backend/tests/test_retrieval_rerank_agent_os.py
git commit -m "$(cat <<'EOF'
feat: Agent OS AI reranker adapter with id validation

EOF
)"
```

---

### Task 4: AgentOSChunkEnricher（TDD）

**Files:**
- Modify: `backend/app/services/retrieval/enricher_agent_os.py`
- Create: `backend/tests/test_retrieval_enricher_agent_os.py`

- [ ] **Step 1: Write failing tests**

```python
import json
import pytest

from app.services.retrieval.enricher_agent_os import (
    RETRIEVAL_CHUNK_ENRICHER_APP_NAME,
    AgentOSChunkEnricher,
    ChunkEnrichResponseError,
)
from app.services.retrieval.types import SegmentDraft


def _seg(chunk_id: str) -> SegmentDraft:
    return SegmentDraft(
        chunk_id=chunk_id,
        node_id="n1",
        parent_node_id=None,
        ancestor_node_ids=[],
        segment_level="fine",
        title_path=["章", "节"],
        start=0,
        end=10,
        text="含授权证书样本",
    )


@pytest.mark.asyncio
async def test_enrich_maps_controlled_tags():
    catalog = [{"name": "授权证书", "aliases": ["授权书"]}]

    async def fake_invoke(app_name, input_data):
        assert app_name == RETRIEVAL_CHUNK_ENRICHER_APP_NAME
        assert input_data["task_id"] == "T1"
        segs = json.loads(input_data["segments_json"])
        assert segs[0]["chunk_id"] == "c1"
        return {
            "segments_json": json.dumps(
                [
                    {
                        "chunk_id": "c1",
                        "title": "授权",
                        "summary": "摘要",
                        "description": "描述",
                        "tags": [{"name": "授权证书", "confidence": 0.9}],
                    }
                ],
                ensure_ascii=False,
            )
        }

    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    out = await enricher.enrich_many(
        task_id="T1", segments=[_seg("c1")], catalog=catalog
    )
    assert out[0].title == "授权"
    assert out[0].summary == "摘要"
    assert out[0].description == "描述"
    assert out[0].tags == [{"name": "授权证书", "confidence": 0.9}]


@pytest.mark.asyncio
async def test_enrich_drops_illegal_tag_names():
    catalog = [{"name": "授权证书", "aliases": []}]

    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {
            "segments_json": json.dumps(
                [
                    {
                        "chunk_id": "c1",
                        "title": "t",
                        "summary": "s",
                        "description": "d",
                        "tags": [
                            {"name": "胡编标签", "confidence": 0.9},
                            {"name": "授权证书", "confidence": 0.8},
                        ],
                    }
                ]
            )
        }

    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    out = await enricher.enrich_many(
        task_id="T1", segments=[_seg("c1")], catalog=catalog
    )
    assert out[0].tags == [{"name": "授权证书", "confidence": 0.8}]


@pytest.mark.asyncio
async def test_enrich_missing_chunk_raises():
    async def fake_invoke(app_name, input_data):
        del app_name, input_data
        return {"segments_json": json.dumps([])}

    enricher = AgentOSChunkEnricher(invoke_app=fake_invoke)
    with pytest.raises(ChunkEnrichResponseError):
        await enricher.enrich_many(
            task_id="T1", segments=[_seg("c1")], catalog=[]
        )
```

- [ ] **Step 2: Fail then implement**

实现要点：

- 常量 `RETRIEVAL_CHUNK_ENRICHER_APP_NAME = "retrieval_chunk_enricher_app"`
- 请求：`task_id`、`catalog_json`、`segments_json`（仅发送 chunk_id/title_path/text/segment_level）
- 响应：解析 `segments_json`；入参每个 `chunk_id` 必须有对应行
- 标签：用 `map_to_controlled_tags([t["name"] for t in tags], catalog=catalog)` 过滤非法名；若模型给了合法名的 confidence，保留该 confidence（在 map 之后按 name 回填，或先过滤再写）
- 写入 `segment.title/summary/description/tags`

置信度保留实现建议：先按 catalog 合法名过滤，再保留原 confidence：

```python
allowed = {row["name"] for row in catalog}
for alias rows...  # 或直接用 map_to_controlled_tags 后再覆盖 confidence
```

最小正确做法：对每条 tag，若 `name` 在 `allowed`（含 aliases 映射后的规范名）则保留，否则丢弃。可用：

```python
from app.services.retrieval.tags import map_to_controlled_tags

mapped = map_to_controlled_tags(
    [str(t.get("name", "")) for t in raw_tags if isinstance(t, dict)],
    catalog=catalog,
)
# then overlay confidence from raw by mapped name when present
```

- [ ] **Step 3: Pass + Commit**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_enricher_agent_os.py -v
git add backend/app/services/retrieval/enricher_agent_os.py \
  backend/tests/test_retrieval_enricher_agent_os.py
git commit -m "$(cat <<'EOF'
feat: Agent OS chunk enricher adapter with controlled tags

EOF
)"
```

---

### Task 5: AgentOSWikiBuilder（聚合 + 文案 + 落库）

**Files:**
- Modify: `backend/app/services/retrieval/wiki_agent_os.py`
- Create: `backend/tests/test_retrieval_wiki_agent_os.py`
- Optional extract: keep grouping helper in `wiki_agent_os.py` as `_group_pages_by_tag`

- [ ] **Step 1: Write failing tests**

使用现有 DB fixture（参考 `tests/test_index_scheduler.py` 建 KnowledgeChunk 的方式）。最小单测可 mock session 行为较难；优先集成式：

```python
import json
import pytest
from sqlalchemy import select

from app.models import KnowledgeChunk, WikiPage
from app.services.retrieval.wiki_agent_os import (
    RETRIEVAL_WIKI_WRITER_APP_NAME,
    AgentOSWikiBuilder,
)


@pytest.mark.asyncio
async def test_wiki_builder_groups_then_writes_copy(db_session):
    # seed one fine chunk with tag 退款政策 @ confidence 0.9
    db_session.add(
        KnowledgeChunk(
            task_id="TW",
            file_id="f1",
            chunk_id="fine-1",
            node_id="n1",
            parent_node_id=None,
            ancestor_node_ids="[]",
            segment_level="fine",
            title="售后",
            summary="七天无理由",
            description="",
            tags=json.dumps(
                [{"name": "退款政策", "confidence": 0.9}], ensure_ascii=False
            ),
            title_path=json.dumps(["售后"], ensure_ascii=False),
            start=0,
            end=10,
            text_ref="",
            child_chunk_ids="[]",
            source="native_text",
            index_status="ready",
            embedding_status="pending",
        )
    )
    await db_session.commit()

    async def fake_invoke(app_name, input_data):
        assert app_name == RETRIEVAL_WIKI_WRITER_APP_NAME
        pages = json.loads(input_data["pages_json"])
        assert pages[0]["tag_name"] == "退款政策"
        assert pages[0]["member_chunk_ids"] == ["fine-1"]
        return {
            "pages_json": json.dumps(
                [
                    {
                        "tag_name": "退款政策",
                        "title": "退款政策主题",
                        "summary": "汇总",
                        "description": "说明",
                    }
                ],
                ensure_ascii=False,
            )
        }

    await AgentOSWikiBuilder(invoke_app=fake_invoke).build_for_task(
        db_session, "TW"
    )
    await db_session.commit()
    rows = (
        await db_session.execute(select(WikiPage).where(WikiPage.task_id == "TW"))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].title == "退款政策主题"
    assert json.loads(rows[0].member_chunk_ids) == ["fine-1"]
```

`KnowledgeChunk` / `db_session` 字段以 `app/models.py` 与现有测试为准；缺列就照 `test_index_scheduler.py` 补全。

另加：`test_wiki_builder_missing_page_copy_raises` —— fake 返回空 `pages_json` → 抛错且不写入。

- [ ] **Step 2: Implement `AgentOSWikiBuilder`**

逻辑：

1. `delete(WikiPage).where(task_id=...)`
2. 查询 `segment_level=="fine"` 且 `index_status=="ready"`
3. 按标签分组（`confidence >= INDEX_TAG_MIN_CONFIDENCE`），与现 `MockWikiBuilder` 相同
4. 若无页可写则 return（不调模型）
5. `invoke_app` 传 `task_id` + `pages_json`
6. 解析响应；每个入参 `tag_name` 必须有文案；缺则 `WikiBuilderResponseError`
7. `session.add(WikiPage(...))`，`member_chunk_ids` / `tags` 用后端聚合值

- [ ] **Step 3: Pass + Commit**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_wiki_agent_os.py -v
git add backend/app/services/retrieval/wiki_agent_os.py \
  backend/tests/test_retrieval_wiki_agent_os.py
git commit -m "$(cat <<'EOF'
feat: Agent OS wiki writer with backend tag grouping

EOF
)"
```

---

### Task 6: 删除生产 Mock、默认工厂切 Agent OS、测试 stub

**Files:**
- Modify: `backend/app/services/retrieval/enricher.py`
- Modify: `backend/app/services/retrieval/wiki.py`
- Modify: `backend/app/services/retrieval/rewrite.py`
- Modify: `backend/app/services/retrieval/rerank.py`
- Modify: `backend/app/config.py`（删除 `AGENT_CHUNK_ENRICHER` 等四行）
- Modify: `backend/app/services/index_scheduler.py`（文档字符串去掉 Mock 字样）
- Modify: `backend/app/services/retrieval/provider.py`（去掉对 Mock* 的无用 import）
- Create: `backend/tests/stubs/retrieval_ai.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_retrieval_tags.py`
- Replace: `backend/tests/test_agent_os_factories.py`

- [ ] **Step 1: Move rule-based Mock bodies into test stubs**

Create `backend/tests/stubs/retrieval_ai.py`：把当前 `MockChunkEnricher` / `MockWikiBuilder` / `MockQueryRewriter` / `MockAiReranker` 类体原样迁入，类名改为 `Stub*`。

- [ ] **Step 2: Slim production modules**

`enricher.py` 最终形态：

```python
from typing import Protocol
from app.services.retrieval.types import SegmentDraft

class ChunkEnricher(Protocol):
    async def enrich_many(self, *, task_id: str, segments: list[SegmentDraft], catalog: list[dict]) -> list[SegmentDraft]: ...

def get_chunk_enricher() -> ChunkEnricher:
    from app.services.retrieval.enricher_agent_os import AgentOSChunkEnricher
    return AgentOSChunkEnricher()
```

`wiki.py`：保留 `WikiBuilder` Protocol、`search_wiki`、`get_wiki_builder()` → `AgentOSWikiBuilder()`；删除 `MockWikiBuilder`。

`rewrite.py` / `rerank.py`：同理只留 Protocol + factory。

`config.py`：删除四行 `AGENT_*`。

- [ ] **Step 3: conftest 默认 stub 四个 factory**

在 `backend/tests/conftest.py` 的 `client` fixture（及任何会跑 index_scheduler 的共享 fixture）中增加：

```python
from tests.stubs.retrieval_ai import (
    StubAiReranker,
    StubChunkEnricher,
    StubQueryRewriter,
    StubWikiBuilder,
)

monkeypatch.setattr(
    "app.services.retrieval.enricher.get_chunk_enricher",
    lambda: StubChunkEnricher(),
)
monkeypatch.setattr(
    "app.services.index_scheduler.get_chunk_enricher",
    lambda: StubChunkEnricher(),
)
monkeypatch.setattr(
    "app.services.retrieval.wiki.get_wiki_builder",
    lambda: StubWikiBuilder(),
)
monkeypatch.setattr(
    "app.services.index_scheduler.get_wiki_builder",
    lambda: StubWikiBuilder(),
)
monkeypatch.setattr(
    "app.services.retrieval.rewrite.get_query_rewriter",
    lambda: StubQueryRewriter(),
)
monkeypatch.setattr(
    "app.services.retrieval.rerank.get_ai_reranker",
    lambda: StubAiReranker(),
)
monkeypatch.setattr(
    "app.services.retrieval.provider.get_query_rewriter",
    lambda: StubQueryRewriter(),
)
monkeypatch.setattr(
    "app.services.retrieval.provider.get_ai_reranker",
    lambda: StubAiReranker(),
)
```

若 `test_index_scheduler` / `test_retrieval_precise` 自有 fixture 不走 `client`，在那些模块的 session fixture 里同样 patch（或抽 `tests/stub_retrieval_ai.py` 的 `apply_retrieval_ai_stubs(monkeypatch)` 函数两处调用）。

- [ ] **Step 4: Update dependent tests**

- `test_retrieval_tags.py`：改 import `StubChunkEnricher`
- `test_agent_os_factories.py`：改为断言 `get_*()` 返回对应 `AgentOS*` 类；删除「缺 client 时 ImportError / 默认 Mock」旧用例

- [ ] **Step 5: Run index + factory tests**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest \
  tests/test_index_scheduler.py \
  tests/test_retrieval_tags.py \
  tests/test_agent_os_factories.py \
  tests/test_retrieval_provider_modes.py \
  tests/test_table_text.py \
  tests/test_retrieval_vectors.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/retrieval/enricher.py \
  backend/app/services/retrieval/wiki.py \
  backend/app/services/retrieval/rewrite.py \
  backend/app/services/retrieval/rerank.py \
  backend/app/config.py \
  backend/app/services/index_scheduler.py \
  backend/app/services/retrieval/provider.py \
  backend/tests/stubs/retrieval_ai.py \
  backend/tests/conftest.py \
  backend/tests/test_retrieval_tags.py \
  backend/tests/test_agent_os_factories.py \
  backend/tests/test_index_scheduler.py \
  backend/tests/test_retrieval_precise.py \
  backend/tests/test_retrieval_provider_modes.py
git commit -m "$(cat <<'EOF'
feat: default retrieval AI factories to Agent OS and stub tests

EOF
)"
```

---

### Task 7: precise_search 硬失败（去掉 degraded 吞异常）

**Files:**
- Modify: `backend/app/services/retrieval/provider.py`
- Modify: `backend/tests/test_retrieval_precise.py`

- [ ] **Step 1: Rewrite the degrade test to expect failure**

Replace `test_precise_search_degrades_when_reranker_fails` with:

```python
@pytest.mark.asyncio
async def test_precise_search_fails_when_reranker_raises(
    provider, monkeypatch, indexed_semantic_task
):
    async def boom(*a, **k):
        raise RuntimeError("rerank down")

    monkeypatch.setattr(
        "app.services.retrieval.provider.ai_rerank_hits",
        boom,
    )
    with pytest.raises(RuntimeError, match="rerank down"):
        await provider.retrieve(
            task_id=indexed_semantic_task,
            content_source="precise_search",
            content_target={"query": "退款"},
        )
```

若现有 monkeypatch 目标是 `provider._ai_rerank`，改为与实现一致：删除 try/except 后直接 await `ai_rerank_hits` / `get_query_rewriter().rewrite`，异常自然抛出。

另加重写失败用例：

```python
@pytest.mark.asyncio
async def test_precise_search_fails_when_rewriter_raises(
    provider, monkeypatch, indexed_semantic_task
):
    class Boom:
        async def rewrite(self, query, hints=None):
            raise RuntimeError("rewrite down")

    monkeypatch.setattr(
        "app.services.retrieval.provider.get_query_rewriter",
        lambda: Boom(),
    )
    with pytest.raises(RuntimeError, match="rewrite down"):
        await provider.retrieve(
            task_id=indexed_semantic_task,
            content_source="precise_search",
            content_target={"query": "退款"},
        )
```

- [ ] **Step 2: Run new tests — expect FAIL while old degrade path still swallows**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_precise.py::test_precise_search_fails_when_reranker_raises -v
```

- [ ] **Step 3: Edit `_precise_search` in provider.py**

删除：

```python
degraded = False
...
try:
    rewrite = await get_query_rewriter().rewrite(query, hints)
except Exception:
    degraded = True
```

改为直接：

```python
rewrite = await get_query_rewriter().rewrite(query, hints)
```

删除 rerank 的 try/except，改为：

```python
reranked_ids = await rerank_fn(session, query or fts_query, candidate_hits)
```

返回 `RetrievalResult` 时 `degraded=False` 固定（或省略，使用 dataclass 默认）。去掉对 `MockAiReranker` / `MockQueryRewriter` 的 import。

- [ ] **Step 4: Pass precise tests + Commit**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest tests/test_retrieval_precise.py -v
git add backend/app/services/retrieval/provider.py \
  backend/tests/test_retrieval_precise.py
git commit -m "$(cat <<'EOF'
fix: fail precise_search when rewrite or rerank errors

EOF
)"
```

---

### Task 8: README Agent OS 调用规范

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 在「说明」节增加专段，并修正过时 Mock 表述**

在 `README.md` 的「## 说明」中：

1. 将「Mock 引擎」改为反映现状：检查项与检索 AI 步骤走 Agent OS；解读/批诊断若仍为 Mock 则如实写。
2. 新增：

```markdown
### Agent OS 调用规范

- **必须复用** `backend/app/services/agent_os.py` 中的 `AgentOSClient.invoke_app`（或向适配器注入同一签名的 `invoke_app`）。
- **禁止**在业务模块直接用 `httpx` / `requests` 等访问 Agent OS 的 `/v1/apps/*`。
- 连接配置：环境变量或项目根 `config.local.json` 的 `agentOs` 块；`docs/agents_config/*.json` 仅为已发布契约快照，不承载密钥。
- 当前已接入应用（运行时以各适配器常量为准）：
  - `tender_doc_interpreter_app`（解读，若已接线）
  - `tender_checklist_generator_app`（检查项生成）
  - `retrieval_chunk_enricher_app` / `retrieval_wiki_writer_app` / `retrieval_query_rewriter_app` / `retrieval_ai_reranker_app`（文档检索）
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: require AgentOSClient reuse for all agent calls

EOF
)"
```

---

### Task 9: 全量回归 + 手工 smoke

**Files:** none（验证）

- [ ] **Step 1: 全量 pytest**

```bash
cd /Users/tongqianni/xlab/tender_application/backend && ../.venv/bin/python -m pytest -q
```

Expected: 全部 PASS

- [ ] **Step 2: 手工 smoke 四个应用（需真实 Agent OS）**

对每个 `appName` 用 `AgentOSClient` 或 curl 打一枪最小合法 input，确认返回可解析 JSON。示例（重写）：

```bash
# 使用项目内 Python，避免手写鉴权分叉
cd /Users/tongqianni/xlab/tender_application && .venv/bin/python - <<'PY'
import asyncio, json
from app.services.agent_os import AgentOSClient

async def main():
    client = AgentOSClient()
    for app, payload in [
        ("retrieval_query_rewriter_app", {
            "query": "是否支持七天无理由退款",
            "hints_json": "[]",
        }),
        ("retrieval_ai_reranker_app", {
            "requirement": "退款",
            "hits_json": json.dumps([
                {"chunk_id": "a", "title": "售后", "summary": "七天无理由", "score": 0.1},
                {"chunk_id": "b", "title": "资质", "summary": "执照", "score": 0.2},
            ], ensure_ascii=False),
        }),
    ]:
        print(app, await client.invoke_app(app, payload))

asyncio.run(main())
PY
```

富化/Wiki 用同样方式各调一次最小 payload。

- [ ] **Step 3: 若 smoke 暴露 schema 不匹配，修适配器或在 Agent OS 控制台修 draft 后重新 publish，并更新 `docs/agents_config` 快照后另提 commit**

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|---|---|
| 发布四应用 + 契约落盘 | Task 1 |
| QueryRewriter / AiReranker / ChunkEnricher / WikiWriter IO | Task 2–5 |
| Wiki 后端聚合、模型只写文案 | Task 5 |
| 复用 AgentOSClient，不新建客户端 | Task 2–5（注入模式） |
| 删除生产 Mock 与开关 | Task 6 |
| 测试 stub / 假 invoke，默认不打真实 OS | Task 6 + 2–5 |
| precise_search 硬失败、去掉 degraded | Task 7 |
| README 强制复用客户端 | Task 8 |
| 回归 + smoke | Task 9 |
| 不改四类 content_source 分流语义 | 无改动 full/collection/large 路径 |

---

## Self-Review Notes

- 无 TBD/TODO 占位；Task 1 依赖交互式 `agent-create-publish` 用户确认，属 skill 硬门禁。
- `RetrievalHit` 构造字段以实现时 `engine/base.py` 为准；计划中测试需按实际签名补齐。
- `load_tag_catalog` 当前无 `description` 字段：enricher 传 `catalog_json` 时用现有 `{name, aliases}` 即可，与规格兼容（description 可选）。
- conftest 必须同时 patch `index_scheduler` 与 `provider` 上的 `get_*` 引用，否则 drain/retrieve 仍走真实 Agent OS。
