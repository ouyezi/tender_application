# Real Batch Diagnosis and API E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace production `MockBatchDiagnosisEngine` with Agent OS category-batch diagnosis, gate file diagnosis on bid index readiness, publish `tender_batch_diagnosis_app`, and add a real-file API E2E script.

**Architecture:** Mirror checklist/interpretation Agent OS clients: new `AgentOSBatchDiagnosisEngine` implements existing `BatchDiagnosisEngine.diagnose_category`. Scheduler waits once for bid `IndexJob.status == ready` when any checklist item is `file`, then retrieves and invokes the agent per category. Mock remains for unit tests via monkeypatch only. E2E script uploads `uploads/T-20260716-005` files and polls to `completed`.

**Tech Stack:** FastAPI, SQLAlchemy asyncio, Agent OS `/v1/apps/invoke`, httpx, pytest-asyncio, argparse script.

**Spec:** `docs/superpowers/specs/2026-07-19-real-batch-diagnosis-and-e2e-design.md`

---

## File Structure

```text
backend/app/
  config.py                              # BATCH_DIAGNOSIS_INDEX_WAIT/POLL defaults
  engine/
    batch_diagnosis_agent_os.py          # NEW AgentOSBatchDiagnosisEngine
    batch_diagnosis_mock.py              # keep for tests only
  services/
    agent_os.py                          # AgentOSSettings + load index wait timeout
    bid_index_wait.py                    # NEW wait_for_bid_index_ready
    scheduler.py                         # wire engine + conditional wait
  services/batch_diagnosis_context.py    # NEW system_instructions builder (optional thin module)

config.local.json.example                # batchDiagnosis.indexWaitTimeoutSeconds

docs/agents_config/
  tender_batch_diagnosis.json            # after create-publish

scripts/
  e2e_diagnosis_flow.py                  # NEW API E2E

backend/tests/
  test_agent_os_client.py                # load settings for index wait
  test_bid_index_wait.py                 # NEW
  test_batch_diagnosis_agent_os.py       # NEW
  test_scheduler.py                      # patch AgentOSBatchDiagnosisEngine; index gate cases
  test_batch_diagnosis.py                # keep Mock tests; optional build_engine helper
```

**执行顺序：** Task 1–4 可独立绿测合入；Task 5 需 Agent OS 鉴权与用户确认草案；Task 6 依赖 Task 4–5 与运行中的服务。

---

### Task 1: 配置索引等待超时

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/services/agent_os.py`
- Modify: `backend/tests/test_agent_os_client.py`
- Modify: `config.local.json.example`

- [ ] **Step 1: Write failing test for index wait timeout load**

在 `backend/tests/test_agent_os_client.py` 追加：

```python
def test_load_settings_batch_diagnosis_index_wait_timeout(tmp_path, monkeypatch):
    import json
    from app.services import agent_os

    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps(
            {
                "agentOs": {"baseUrl": "http://localhost:8000"},
                "batchDiagnosis": {"indexWaitTimeoutSeconds": 3600},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", cfg)
    monkeypatch.delenv("BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS", raising=False)
    settings = agent_os.load_settings()
    assert settings.batch_diagnosis_index_wait_timeout_seconds == 3600.0

    monkeypatch.setenv("BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS", "120")
    settings = agent_os.load_settings()
    assert settings.batch_diagnosis_index_wait_timeout_seconds == 120.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_batch_diagnosis_index_wait_timeout -v`

Expected: FAIL（`AgentOSSettings` 无该字段）

- [ ] **Step 3: Implement config + settings**

在 `backend/app/config.py` 增加：

```python
BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS = 7200.0
BATCH_DIAGNOSIS_INDEX_POLL_SECONDS = 2.0
```

在 `AgentOSSettings` 增加字段：

```python
batch_diagnosis_index_wait_timeout_seconds: float = 7200.0
```

在 `load_settings()` 中读取 `batchDiagnosis.indexWaitTimeoutSeconds`，环境变量 `BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS`，默认 `7200`。

`config.local.json.example` 增加：

```json
{
  "agentOs": {
    "baseUrl": "http://localhost:8000",
    "timeoutSeconds": 180,
    "maxAttempts": 3,
    "auth": {
      "cookie": "",
      "headerName": "",
      "headerValue": ""
    }
  },
  "tenderInterpretation": {
    "parseWaitTimeoutSeconds": 1800
  },
  "batchDiagnosis": {
    "indexWaitTimeoutSeconds": 7200
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_batch_diagnosis_index_wait_timeout -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/app/services/agent_os.py \
  backend/tests/test_agent_os_client.py config.local.json.example
git commit -m "feat: add batch diagnosis index wait timeout config"
```

---

### Task 2: `wait_for_bid_index_ready`

**Files:**
- Create: `backend/app/services/bid_index_wait.py`
- Create: `backend/tests/test_bid_index_wait.py`

- [ ] **Step 1: Write failing tests**

创建 `backend/tests/test_bid_index_wait.py`：

```python
import pytest
from datetime import datetime, timezone

from app import db
from app.models import DiagnosisTask, IndexJob
from app.services.bid_index_wait import (
    BidIndexBlockedError,
    wait_for_bid_index_ready,
)


@pytest.mark.asyncio
async def test_wait_returns_when_bid_index_ready(client, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-ready",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id="bid-1",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            IndexJob(
                task_id="t-idx-ready",
                file_id="bid-1",
                status="ready",
                stage="wiki",
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    await wait_for_bid_index_ready("t-idx-ready", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_fails_when_bid_index_failed(client, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-fail",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id="bid-2",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            IndexJob(
                task_id="t-idx-fail",
                file_id="bid-2",
                status="failed",
                stage="enrich",
                error_message="enrich boom",
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    with pytest.raises(BidIndexBlockedError, match="bid_index_failed"):
        await wait_for_bid_index_ready("t-idx-fail", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_fails_when_bid_file_id_missing(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-missing",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id=None,
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    with pytest.raises(BidIndexBlockedError, match="bid_file_missing"):
        await wait_for_bid_index_ready("t-idx-missing", timeout=1.0)


@pytest.mark.asyncio
async def test_wait_times_out_when_still_queued(db_session, monkeypatch):
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="t-idx-timeout",
                tender_filename="t.docx",
                tender_path="t.docx",
                bid_filename="b.docx",
                bid_path="b.docx",
                bid_file_id="bid-3",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            IndexJob(
                task_id="t-idx-timeout",
                file_id="bid-3",
                status="queued",
                stage="segments",
            )
        )
        await session.commit()

    monkeypatch.setattr("app.config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS", 0.01)
    with pytest.raises(BidIndexBlockedError, match="bid_index_timeout"):
        await wait_for_bid_index_ready("t-idx-timeout", timeout=0.05)
```

这些用例依赖 `client` fixture（见 `conftest.py`）：它会 patch `app.db.SessionLocal` 并建表。测试函数签名加 `client`，即使体内不用 `client` 变量也要保留该参数。

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_bid_index_wait.py -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement `wait_for_bid_index_ready`**

创建 `backend/app/services/bid_index_wait.py`：

```python
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app import config
from app import db as database
from app.models import DiagnosisTask, IndexJob


class BidIndexBlockedError(RuntimeError):
    pass


async def wait_for_bid_index_ready(
    task_id: str,
    timeout: float | None = None,
) -> None:
    wait_timeout = (
        float(timeout)
        if timeout is not None
        else float(config.BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS)
    )
    # Prefer AgentOSSettings override when available
    try:
        from app.services.agent_os import load_settings

        wait_timeout = (
            float(timeout)
            if timeout is not None
            else float(load_settings().batch_diagnosis_index_wait_timeout_seconds)
        )
    except Exception:
        pass

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_timeout
    poll = float(config.BATCH_DIAGNOSIS_INDEX_POLL_SECONDS)

    while True:
        async with database.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise BidIndexBlockedError("task_missing")
            bid_file_id = task.bid_file_id
            if not bid_file_id:
                raise BidIndexBlockedError("bid_file_missing")

            result = await session.execute(
                select(IndexJob)
                .where(
                    IndexJob.task_id == task_id,
                    IndexJob.file_id == bid_file_id,
                )
                .order_by(IndexJob.id.desc())
            )
            job = result.scalars().first()
            if job is not None:
                if job.status == "ready":
                    return
                if job.status == "failed":
                    detail = job.error_message or ""
                    raise BidIndexBlockedError(
                        f"bid_index_failed:{detail}" if detail else "bid_index_failed"
                    )

        if loop.time() >= deadline:
            raise BidIndexBlockedError("bid_index_timeout")
        await asyncio.sleep(poll)
```

实现时去掉宽泛 `except Exception`：仅在 `timeout is None` 时调用 `load_settings()`，失败则回退 `config.BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_bid_index_wait.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/bid_index_wait.py backend/tests/test_bid_index_wait.py
git commit -m "feat: wait for bid knowledge index before file diagnosis"
```

---

### Task 3: `AgentOSBatchDiagnosisEngine`

**Files:**
- Create: `backend/app/engine/batch_diagnosis_agent_os.py`
- Create: `backend/app/services/batch_diagnosis_context.py`
- Create: `backend/tests/test_batch_diagnosis_agent_os.py`

- [ ] **Step 1: Write failing tests for parse + invoke**

创建 `backend/tests/test_batch_diagnosis_agent_os.py`：

```python
import pytest

from app.engine.base import RetrievedChunk
from app.engine.batch_diagnosis_agent_os import (
    AgentOSBatchDiagnosisEngine,
    BatchDiagnosisResponseError,
    parse_batch_diagnosis_payload,
)


def test_parse_batch_diagnosis_payload_ok():
    draft = parse_batch_diagnosis_payload(
        {
            "schema_version": "1",
            "results": [
                {
                    "checklist_item_id": "i1",
                    "compliance": "satisfied",
                    "consequence_tags": ["score_risk"],
                    "evidence": "见第3页营业执照",
                    "suggestion": "保持现有材料",
                    "description": "执照齐全",
                }
            ],
        }
    )
    assert len(draft) == 1
    assert draft[0].checklist_item_id == "i1"
    assert draft[0].compliance == "satisfied"


def test_parse_rejects_bad_compliance():
    with pytest.raises(BatchDiagnosisResponseError, match="compliance"):
        parse_batch_diagnosis_payload(
            {
                "schema_version": "1",
                "results": [
                    {
                        "checklist_item_id": "i1",
                        "compliance": "maybe",
                        "consequence_tags": [],
                        "evidence": "e",
                        "suggestion": "s",
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_diagnose_category_invokes_app():
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        async def invoke_app(self, app_name: str, input_data: dict):
            calls.append((app_name, input_data))
            return {
                "schema_version": "1",
                "results": [
                    {
                        "checklist_item_id": "i1",
                        "compliance": "insufficient_evidence",
                        "consequence_tags": [],
                        "evidence": "检索块不足",
                        "suggestion": "补充材料",
                        "description": "无法判定",
                    }
                ],
            }

    engine = AgentOSBatchDiagnosisEngine(client=FakeClient())
    results = await engine.diagnose_category(
        task_id="T-1",
        category={"id": "c1", "name": "资格", "description": ""},
        items=[
            {
                "id": "i1",
                "title": "执照",
                "requirement": "提供执照",
                "technique": "查附件",
                "importance": "high",
                "compliance_rules": {
                    "satisfied": "有执照",
                    "violated": "无执照",
                    "cannot_satisfy": "无法提供",
                    "insufficient_evidence": "看不清",
                },
                "consequence_rules": {"bid_unusable": "废标"},
                "expected_evidence": "营业执照扫描件",
            }
        ],
        retrieved_chunks=[RetrievedChunk(chunk_id="ch1", text="执照复印件", location="p1")],
    )
    assert calls[0][0] == "tender_batch_diagnosis_app"
    assert "system_instructions" in calls[0][1]
    assert "category_payload" in calls[0][1]
    assert "retrieved_chunks" in calls[0][1]
    assert results[0].compliance == "insufficient_evidence"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_batch_diagnosis_agent_os.py -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement context + engine**

创建 `backend/app/services/batch_diagnosis_context.py`：

```python
SYSTEM_INSTRUCTIONS = """你是标书合规批诊断助手。
根据分类下的检查项与检索到的标书内容块，为每条检查项给出合规判定。
规则：
1. 只依据 retrieved_chunks 与检查项 requirement/compliance_rules 判定；禁止臆造未出现的证据。
2. compliance 只能是 satisfied|violated|cannot_satisfy|insufficient_evidence。
3. consequence_tags 只能来自 no_score|bid_unusable|score_risk|general_risk；可为空列表。
4. results 必须覆盖 category_payload.items 中每一个 checklist_item_id，禁止漏项或多余项。
5. schema_version 必须为 "1"。
6. 严格输出符合 outputSchema 的 JSON 对象，不要输出额外说明文字。
"""
```

创建 `backend/app/engine/batch_diagnosis_agent_os.py`（要点）：

```python
from __future__ import annotations

import json
from typing import Any, Protocol

from app.engine.base import BatchItemResult, RetrievedChunk
from app.services.batch_diagnosis_context import SYSTEM_INSTRUCTIONS

TENDER_BATCH_DIAGNOSIS_APP_NAME = "tender_batch_diagnosis_app"

_COMPLIANCE = frozenset(
    {"satisfied", "violated", "cannot_satisfy", "insufficient_evidence"}
)
_TAGS = frozenset({"no_score", "bid_unusable", "score_risk", "general_risk"})


class AgentOSInvoker(Protocol):
    async def invoke_app(
        self, app_name: str, input_data: dict[str, object]
    ) -> dict[str, Any]: ...


class BatchDiagnosisResponseError(ValueError):
    pass


def parse_batch_diagnosis_payload(payload: dict[str, Any]) -> list[BatchItemResult]:
    if not isinstance(payload, dict):
        raise BatchDiagnosisResponseError("payload must be object")
    if payload.get("schema_version") != "1":
        raise BatchDiagnosisResponseError("schema_version invalid")
    results_raw = payload.get("results")
    if not isinstance(results_raw, list) or not results_raw:
        raise BatchDiagnosisResponseError("missing or empty results")
    out: list[BatchItemResult] = []
    for row in results_raw:
        if not isinstance(row, dict):
            raise BatchDiagnosisResponseError("result row must be object")
        item_id = str(row.get("checklist_item_id") or "").strip()
        compliance = str(row.get("compliance") or "").strip()
        if not item_id:
            raise BatchDiagnosisResponseError("checklist_item_id missing")
        if compliance not in _COMPLIANCE:
            raise BatchDiagnosisResponseError("compliance invalid")
        tags_raw = row.get("consequence_tags") or []
        if not isinstance(tags_raw, list):
            raise BatchDiagnosisResponseError("consequence_tags must be list")
        tags: list[str] = []
        for tag in tags_raw:
            t = str(tag).strip()
            if t not in _TAGS:
                raise BatchDiagnosisResponseError("consequence_tags invalid")
            if t not in tags:
                tags.append(t)
        out.append(
            BatchItemResult(
                checklist_item_id=item_id,
                compliance=compliance,
                consequence_tags=tags,
                evidence=str(row.get("evidence") or ""),
                suggestion=str(row.get("suggestion") or ""),
                description=str(row.get("description") or ""),
            )
        )
    return out


class AgentOSBatchDiagnosisEngine:
    def __init__(
        self,
        client: AgentOSInvoker,
        *,
        app_name: str = TENDER_BATCH_DIAGNOSIS_APP_NAME,
    ) -> None:
        self._client = client
        self._app_name = app_name

    async def diagnose_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[BatchItemResult]:
        del task_id
        category_payload = {
            "category": {
                "id": category.get("id"),
                "name": category.get("name"),
                "description": category.get("description", ""),
            },
            "items": items,
        }
        chunks_payload = [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "location": c.location,
            }
            for c in retrieved_chunks
        ]
        response = await self._client.invoke_app(
            self._app_name,
            {
                "system_instructions": SYSTEM_INSTRUCTIONS,
                "category_payload": json.dumps(
                    category_payload, ensure_ascii=False
                ),
                "retrieved_chunks": json.dumps(
                    chunks_payload, ensure_ascii=False
                ),
            },
        )
        return parse_batch_diagnosis_payload(response)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/bin/python -m pytest tests/test_batch_diagnosis_agent_os.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/batch_diagnosis_agent_os.py \
  backend/app/services/batch_diagnosis_context.py \
  backend/tests/test_batch_diagnosis_agent_os.py
git commit -m "feat: add Agent OS batch diagnosis engine"
```

---

### Task 4: 接线 scheduler（索引门闩 + 真实引擎）

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Update offline-mix test to patch new engine class**

将 `test_scheduler.py` 中：

```python
monkeypatch.setattr(scheduler, "MockBatchDiagnosisEngine", E)
```

改为：

```python
monkeypatch.setattr(scheduler, "AgentOSBatchDiagnosisEngine", E)
```

并确保 import 侧 scheduler 使用 `AgentOSBatchDiagnosisEngine`（见 Step 3）。若 `E` 的 `__init__` 需接受 `client=`，改为 `def __init__(self, *a, **k): pass`（已有）。

- [ ] **Step 2: Add failing tests for index gate**

在 `test_scheduler.py` 追加（风格对齐 `test_run_diagnosis_phase_mixed_modes`，依赖 `client` fixture 以初始化 DB）：

```python
@pytest.mark.asyncio
async def test_diagnosis_fails_when_bid_index_wait_blocked(monkeypatch, client):
    from datetime import datetime, timezone

    from app.models import DiagnosisTask
    from app.services import scheduler
    from app.services.bid_index_wait import BidIndexBlockedError

    async def boom(task_id, timeout=None):
        del task_id, timeout
        raise BidIndexBlockedError("bid_index_timeout")

    wait_calls = {"n": 0}

    async def tracked_wait(task_id, timeout=None):
        wait_calls["n"] += 1
        return await boom(task_id, timeout)

    monkeypatch.setattr(scheduler, "wait_for_bid_index_ready", tracked_wait)

    async def fake_report(task_id):
        del task_id
        return {
            "categories": [
                {
                    "id": "c1",
                    "name": "c",
                    "items": [
                        {
                            "id": "file-1",
                            "title": "执照",
                            "requirement": "执照",
                            "diagnosis_mode": "file",
                            "consequence_rules": {},
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(scheduler, "get_report", fake_report)

    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="task-idx-gate",
                tender_filename="t.pdf",
                tender_path="t.pdf",
                bid_filename="b.docx",
                bid_path="b.docx",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    assert await scheduler._run_diagnosis_phase("task-idx-gate") is False
    assert wait_calls["n"] == 1
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, "task-idx-gate")
        assert task is not None
        assert task.status == "failed"
        assert task.failure_stage == "diagnosing"
        assert "bid_index_timeout" in (task.error_message or "")


@pytest.mark.asyncio
async def test_diagnosis_skips_index_wait_when_all_offline(monkeypatch, client):
    from datetime import datetime, timezone

    from app.models import DiagnosisTask
    from app.services import scheduler

    wait_calls = {"n": 0}

    async def tracked_wait(task_id, timeout=None):
        del task_id, timeout
        wait_calls["n"] += 1

    monkeypatch.setattr(scheduler, "wait_for_bid_index_ready", tracked_wait)

    class E:
        def __init__(self, *a, **k):
            pass

        async def diagnose_category(self, **kwargs):
            raise AssertionError("engine should not run for all-offline")

    monkeypatch.setattr(scheduler, "AgentOSBatchDiagnosisEngine", E)

    async def fake_report(task_id):
        del task_id
        return {
            "categories": [
                {
                    "id": "c1",
                    "name": "c",
                    "items": [
                        {
                            "id": "offline-1",
                            "title": "密封",
                            "requirement": "密封",
                            "diagnosis_mode": "offline",
                            "consequence_rules": {},
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(scheduler, "get_report", fake_report)

    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id="task-all-offline",
                tender_filename="t.pdf",
                tender_path="t.pdf",
                bid_filename="b.docx",
                bid_path="b.docx",
                status="diagnosing",
                progress_done=0,
                progress_total=1,
                background="",
                requirements="",
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    assert await scheduler._run_diagnosis_phase("task-all-offline") is True
    assert wait_calls["n"] == 0
```

确保文件顶部已 `from app import db`（与同文件其它用例一致）。

- [ ] **Step 3: Wire scheduler**

在 `scheduler.py`：

1. 删除生产对 `MockBatchDiagnosisEngine` / `MOCK_BATCH_DIAGNOSIS_DELAY_SECONDS` 的依赖。
2. 增加：

```python
from app.engine.batch_diagnosis_agent_os import AgentOSBatchDiagnosisEngine
from app.services.bid_index_wait import BidIndexBlockedError, wait_for_bid_index_ready
```

3. 在 `_run_diagnosis_phase` 开头（拿到 `categories` 后）：

```python
has_file_items = any(
    (item.get("diagnosis_mode") or "file") != "offline"
    for category in categories
    for item in category["items"]
)
if has_file_items:
    try:
        await wait_for_bid_index_ready(task_id)
    except BidIndexBlockedError as exc:
        await _mark_failed(task_id, str(exc), "diagnosing")
        return False
```

4. 引擎构造改为：

```python
engine = AgentOSBatchDiagnosisEngine(client=AgentOSClient())
```

（与解读/检查项一致；测试通过 monkeypatch `AgentOSBatchDiagnosisEngine` 替换。）

5. `AgentOSConfigError` / `BatchDiagnosisResponseError` / `assert_batch_complete` 失败时 `_mark_failed(..., "diagnosing")` 并 `return False`（对齐现有异常处理模式；若当前 Mock 路径靠异常冒泡，改为显式捕获并失败，避免未处理异常拖垮 worker）。

- [ ] **Step 4: Run scheduler + batch tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_scheduler.py \
  tests/test_batch_diagnosis.py \
  tests/test_batch_diagnosis_agent_os.py \
  tests/test_bid_index_wait.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduler.py backend/tests/test_scheduler.py
git commit -m "feat: use Agent OS batch diagnosis and bid index gate"
```

---

### Task 5: 创建并发布 Agent OS 诊断应用

**Files:**
- Create: `docs/agents_config/tender_batch_diagnosis.json`（发布成功后落盘）
- Skill: `.cursor/skills/agent-create-publish/SKILL.md`

- [ ] **Step 1: 准备 create 草案（给用户确认）**

使用 `agent-create-publish` skill。**硬门禁：用户确认前禁止写接口。**

草案要点：

| 字段 | 值 |
|---|---|
| zhName | 标书分类批诊断助手 |
| enName | `tender_batch_diagnosis` |
| app enName | `tender_batch_diagnosis_app` |
| mode | api / sync |
| timeoutMs | ≥ 180000 |
| model | 与 checklist 同级（如 `qwen3.6-flash`，以 Step 0 模型列表为准） |

**inputSchema（3 个 string）：** `system_instructions`, `category_payload`, `retrieved_chunks`

**outputSchema：**

```json
{
  "type": "object",
  "required": ["schema_version", "results"],
  "properties": {
    "schema_version": { "type": "string" },
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "required": [
          "checklist_item_id",
          "compliance",
          "consequence_tags",
          "evidence",
          "suggestion"
        ],
        "properties": {
          "checklist_item_id": { "type": "string" },
          "compliance": { "type": "string" },
          "consequence_tags": {
            "type": "array",
            "items": { "type": "string" }
          },
          "evidence": { "type": "string" },
          "suggestion": { "type": "string" },
          "description": { "type": "string" }
        }
      }
    }
  }
}
```

**systemPrompt 模板：**

```text
你是标书合规批诊断助手。

## 固定判定规则与输出约束
{{system_instructions}}

## 当前分类与检查项（JSON）
{{category_payload}}

## 检索到的标书内容块（JSON）
{{retrieved_chunks}}

## 职责
1. 仅为 category_payload.items 中每条检查项输出一条 results。
2. 依据 retrieved_chunks 判定；证据不足用 insufficient_evidence。
3. compliance / consequence_tags 遵守 system_instructions 枚举。
4. schema_version 必须为 "1"。
5. 严格输出符合 outputSchema 的 JSON，不要额外说明。
```

- [ ] **Step 2: 用户确认后执行 create + publish**

按 skill 调用管理 API；成功后将完整配置写入
`docs/agents_config/tender_batch_diagnosis.json`。

确认 `invoke.appName == "tender_batch_diagnosis_app"`，与
`TENDER_BATCH_DIAGNOSIS_APP_NAME` 一致。

- [ ] **Step 3: 最小 invoke 冒烟（可选但推荐）**

用 `AgentOSClient` 或 curl 对 app 打一枪最小合法 input，确认返回可解析 JSON。

- [ ] **Step 4: Commit 落盘配置**

```bash
git add docs/agents_config/tender_batch_diagnosis.json
git commit -m "chore: add tender_batch_diagnosis agent config snapshot"
```

---

### Task 6: API E2E 脚本

**Files:**
- Create: `scripts/e2e_diagnosis_flow.py`

- [ ] **Step 1: Implement script**

```python
#!/usr/bin/env python3
"""Real-file API E2E for tender diagnosis flow.

Prerequisites:
  - startup.py running (API on --base-url)
  - config.local.json with Agent OS
  - tender_batch_diagnosis_app published

Example:
  .venv/bin/python scripts/e2e_diagnosis_flow.py
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TENDER = ROOT / "uploads" / "T-20260716-005" / "tender.docx"
DEFAULT_BID = ROOT / "uploads" / "T-20260716-005" / "bid.docx"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    parser.add_argument("--tender", type=Path, default=DEFAULT_TENDER)
    parser.add_argument("--bid", type=Path, default=DEFAULT_BID)
    parser.add_argument("--timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if not args.tender.is_file() or not args.bid.is_file():
        print(f"missing files: {args.tender} / {args.bid}", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=120.0) as client:
        with args.tender.open("rb") as tf, args.bid.open("rb") as bf:
            resp = client.post(
                "/api/tasks",
                files={
                    "tender_file": (args.tender.name, tf),
                    "bid_file": (args.bid.name, bf),
                },
                data={"background": "", "requirements": ""},
            )
        resp.raise_for_status()
        task = resp.json()
        task_id = task["id"]
        print(f"created task {task_id}")

        deadline = time.time() + args.timeout_seconds
        detail = task
        while time.time() < deadline:
            detail = client.get(f"/api/tasks/{task_id}").json()
            status = detail.get("status")
            print(
                f"status={status} stage={detail.get('failure_stage')} "
                f"progress={detail.get('progress_done')}/{detail.get('progress_total')}"
            )
            if status in {"completed", "failed", "stopped"}:
                break
            time.sleep(args.poll_seconds)
        else:
            print("e2e timeout waiting for terminal status", file=sys.stderr)
            return 1

        if detail.get("status") != "completed":
            print(
                f"FAILED status={detail.get('status')} "
                f"failure_stage={detail.get('failure_stage')} "
                f"error={detail.get('error_message')}",
                file=sys.stderr,
            )
            return 1

        checklist = client.get(f"/api/tasks/{task_id}/checklist").json()
        items = [
            item
            for cat in checklist.get("categories", [])
            for item in cat.get("items", [])
        ]
        if not items:
            print("checklist empty", file=sys.stderr)
            return 1

        results = detail.get("results") or []
        if len(results) < 1:
            print("results empty", file=sys.stderr)
            return 1
        file_items = [
            i for i in items if (i.get("diagnosis_mode") or "file") != "offline"
        ]
        if file_items:
            for row in results:
                evidence = str(row.get("evidence") or "")
                if "mock evidence for checklist item" in evidence.lower():
                    print("mock evidence detected in results", file=sys.stderr)
                    return 1

        report = client.get(f"/api/tasks/{task_id}/report.docx")
        if report.status_code != 200:
            print(f"report.docx status {report.status_code}", file=sys.stderr)
            return 1

        print(f"E2E OK task={task_id} items={len(items)} results={len(results)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

若 `GET /api/tasks/{id}` 的 `results` 字段名不同，按 `schemas.py` / `TaskDetailOut` 实际字段调整断言（例如从独立 results 端点或 detail 嵌套读取）。实现前先读 `backend/app/schemas.py` 与 `tasks.py` 的 detail 序列化，把脚本对齐真实响应。

- [ ] **Step 2: Dry-run import check**

Run: `.venv/bin/python -c "import scripts.e2e_diagnosis_flow"` 或
`.venv/bin/python scripts/e2e_diagnosis_flow.py --help`

Expected: 打印帮助，退出 0

- [ ] **Step 3: Commit**

```bash
git add scripts/e2e_diagnosis_flow.py
git commit -m "test: add real-file API e2e diagnosis script"
```

- [ ] **Step 4: Run full E2E（需服务与 Agent 已就绪）**

```bash
# terminal A
.venv/bin/python startup.py

# terminal B（可能数小时）
.venv/bin/python scripts/e2e_diagnosis_flow.py
```

Expected: 打印 `E2E OK ...`，退出码 0。失败则根据 `failure_stage` 排查。

---

### Task 7: 全量单测回归与 README 纠偏（可选小改）

**Files:**
- Modify: `README.md`（若仍写「解读/诊断为 Mock」，改为与现状一致的一句说明）

- [ ] **Step 1: Run full backend pytest**

Run: `cd backend && ../.venv/bin/python -m pytest -q`

Expected: PASS

- [ ] **Step 2: README 一句纠偏（仅当存在过时 Mock 表述）**

将「批诊断仍为 Mock」改为「批诊断走 Agent OS `tender_batch_diagnosis_app`；E2E：`scripts/e2e_diagnosis_flow.py`」。

- [ ] **Step 3: Commit if README changed**

```bash
git add README.md
git commit -m "docs: note real batch diagnosis and e2e script"
```

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|---|---|
| AgentOSBatchDiagnosisEngine | Task 3 |
| wait_for_bid_index_ready + 仅 file 项时 wait | Task 2, 4 |
| 生产无 Mock 回退 | Task 4 |
| 配置 indexWaitTimeoutSeconds 默认 7200 | Task 1 |
| create-publish + 落盘 agents_config | Task 5 |
| scripts/e2e_diagnosis_flow.py + 默认样例路径 | Task 6 |
| 单测 + 真实文件验收顺序 | Task 4, 6, 7 |

## 明确不做（计划内不出现）

- 逐项诊断 / 两阶段证据抽取
- `DIAGNOSIS_ENGINE` 配置开关
- 入库 1GB 样例文件
- UI 浏览器自动化
