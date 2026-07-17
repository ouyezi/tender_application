# Agent OS 检查项生成接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 发布 `tender_checklist_generator` 应用，将 `generating_checklist` 的大模型调用切到 Agent OS，删除 `MockChecklistAgent`，并把已发布配置落到 `docs/agents_config`。

**Architecture:** 复用/落地通用 `AgentOSClient.invoke_app`；`ChecklistContextBuilder` 产出显式 `ChecklistCallInput`；`AgentOSChecklistAgent` 按分片调用 `tender_checklist_generator_app`，后端纯函数合并后走现有 `ChecklistService.validate_draft` 落库。

**Tech Stack:** Python 3 / FastAPI / httpx / pytest / Agent OS `POST /v1/apps/invoke` / `agent-create-publish` skill

**Spec:** `docs/superpowers/specs/2026-07-17-agent-os-checklist-generation-design.md`

---

## File Structure

| Path | Responsibility |
|---|---|
| `backend/app/services/agent_os.py` | Agent OS 连接配置 + `AgentOSClient.invoke_app`（若已由解读计划落地则复用，勿重复造） |
| `config.local.json.example` | 无密钥示例：`agentOs` 块 |
| `backend/app/services/checklist_context.py` | `ChecklistCallInput` + 显式字段上下文；`SYSTEM_INSTRUCTIONS` 常量 |
| `backend/app/engine/checklist_merge.py` | 跨片合并纯函数 |
| `backend/app/engine/checklist_agent_os.py` | `AgentOSChecklistAgent`：解析响应、调合并 |
| `backend/app/services/scheduler.py` | 注入 `AgentOSChecklistAgent` |
| `backend/app/config.py` | `CHECKLIST_AGENT = "agent_os"` |
| `docs/agents_config/tender_checklist_generator.json` | 已发布契约快照 |
| 删除 `backend/app/engine/checklist_mock.py` | Mock 实现移除 |

---

### Task 1: Agent OS settings loader

**Files:**
- Create: `backend/app/services/agent_os.py`
- Create: `backend/tests/test_agent_os_client.py`
- Create: `config.local.json.example`（若不存在）
- Modify: `.gitignore`
- Skip entire Task 1–2 if `backend/app/services/agent_os.py` already exports `load_settings` and `AgentOSClient.invoke_app` — run their existing tests and jump to Task 3.

- [ ] **Step 1: Write failing settings tests**

Create `backend/tests/test_agent_os_client.py`:

```python
import json

from app.services import agent_os


def test_load_settings_env_overrides_local_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps(
            {
                "agentOs": {
                    "baseUrl": "http://from-file",
                    "timeoutSeconds": 10,
                    "maxAttempts": 2,
                    "auth": {
                        "cookie": "file-cookie",
                        "headerName": "X-File",
                        "headerValue": "file-value",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", cfg)
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://from-env")
    monkeypatch.setenv("AGENT_OS_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("AGENT_OS_AUTH_COOKIE", "env-cookie")
    monkeypatch.setenv("AGENT_OS_AUTH_HEADER_NAME", "X-Env")
    monkeypatch.setenv("AGENT_OS_AUTH_HEADER_VALUE", "env-value")

    settings = agent_os.load_settings()
    assert settings.base_url == "http://from-env"
    assert settings.timeout_seconds == 99.0
    assert settings.max_attempts == 5
    assert settings.auth_cookie == "env-cookie"
    assert settings.auth_header_name == "X-Env"
    assert settings.auth_header_value == "env-value"


def test_load_settings_falls_back_to_local_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps(
            {
                "agentOs": {
                    "baseUrl": "http://localhost:8000",
                    "timeoutSeconds": 180,
                    "maxAttempts": 3,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", cfg)
    for key in (
        "AGENT_OS_BASE_URL",
        "AGENT_OS_TIMEOUT_SECONDS",
        "AGENT_OS_MAX_ATTEMPTS",
        "AGENT_OS_AUTH_COOKIE",
        "AGENT_OS_AUTH_HEADER_NAME",
        "AGENT_OS_AUTH_HEADER_VALUE",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = agent_os.load_settings()
    assert settings.base_url == "http://localhost:8000"
    assert settings.timeout_seconds == 180.0
    assert settings.max_attempts == 3


def test_load_settings_missing_base_url_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    for key in ("AGENT_OS_BASE_URL",):
        monkeypatch.delenv(key, raising=False)
    settings = agent_os.load_settings()
    assert settings.base_url == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_env_overrides_local_json tests/test_agent_os_client.py::test_load_settings_falls_back_to_local_json tests/test_agent_os_client.py::test_load_settings_missing_base_url_is_empty -v
```

Expected: FAIL with `ImportError` or `AttributeError` for `app.services.agent_os`.

- [ ] **Step 3: Implement settings**

Create `backend/app/services/agent_os.py`:

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.config import ROOT

LOCAL_CONFIG_PATH = ROOT / "config.local.json"


@dataclass(frozen=True)
class AgentOSSettings:
    base_url: str
    timeout_seconds: float = 180.0
    max_attempts: int = 3
    auth_cookie: str = ""
    auth_header_name: str = ""
    auth_header_value: str = ""


class AgentOSError(Exception):
    def __init__(
        self,
        message: str,
        *,
        app_name: str = "",
        status_code: Optional[int] = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.app_name = app_name
        self.status_code = status_code
        self.retryable = retryable


class AgentOSConfigError(AgentOSError):
    pass


class AgentOSResponseError(AgentOSError):
    pass


def _read_local_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _env_or(local_value: Any, env_key: str, default: Any) -> Any:
    raw = os.environ.get(env_key)
    if raw is not None and raw != "":
        return raw
    if local_value is not None and local_value != "":
        return local_value
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_settings(path: Optional[Path] = None) -> AgentOSSettings:
    cfg_path = path if path is not None else LOCAL_CONFIG_PATH
    local = _read_local_config(cfg_path)
    agent_os = local.get("agentOs") if isinstance(local.get("agentOs"), dict) else {}
    auth = agent_os.get("auth") if isinstance(agent_os.get("auth"), dict) else {}
    return AgentOSSettings(
        base_url=str(_env_or(agent_os.get("baseUrl"), "AGENT_OS_BASE_URL", "")).rstrip(
            "/"
        ),
        timeout_seconds=_as_float(
            _env_or(agent_os.get("timeoutSeconds"), "AGENT_OS_TIMEOUT_SECONDS", 180),
            180.0,
        ),
        max_attempts=_as_int(
            _env_or(agent_os.get("maxAttempts"), "AGENT_OS_MAX_ATTEMPTS", 3),
            3,
        ),
        auth_cookie=str(_env_or(auth.get("cookie"), "AGENT_OS_AUTH_COOKIE", "")),
        auth_header_name=str(
            _env_or(auth.get("headerName"), "AGENT_OS_AUTH_HEADER_NAME", "")
        ),
        auth_header_value=str(
            _env_or(auth.get("headerValue"), "AGENT_OS_AUTH_HEADER_VALUE", "")
        ),
    )
```

Create `config.local.json.example` at repo root if missing:

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
  }
}
```

Append to `.gitignore` if not present:

```gitignore
config.local.json
```

- [ ] **Step 4: Run settings tests**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_env_overrides_local_json tests/test_agent_os_client.py::test_load_settings_falls_back_to_local_json tests/test_agent_os_client.py::test_load_settings_missing_base_url_is_empty -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_os.py backend/tests/test_agent_os_client.py config.local.json.example .gitignore
git commit -m "$(cat <<'EOF'
feat: add Agent OS settings loader for checklist invoke

EOF
)"
```

---

### Task 2: AgentOSClient.invoke_app

**Files:**
- Modify: `backend/app/services/agent_os.py`
- Modify: `backend/tests/test_agent_os_client.py`

- [ ] **Step 1: Append failing client tests**

```python
import httpx
import pytest

from app.services import agent_os


@pytest.mark.asyncio
async def test_invoke_app_posts_app_name_and_input(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"schema_version": "1", "categories": [], "items": []})

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    result = await client.invoke_app(
        "tender_checklist_generator_app",
        {"tender_segment": "正文"},
    )
    assert captured["url"] == "http://agent-os.test/v1/apps/invoke"
    assert captured["json"]["appName"] == "tender_checklist_generator_app"
    assert captured["json"]["input"]["tender_segment"] == "正文"
    assert result["schema_version"] == "1"


@pytest.mark.asyncio
async def test_invoke_app_retries_retryable_status(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(agent_os, "_backoff_seconds", lambda attempt: 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    result = await client.invoke_app("demo_app", {"q": "1"})
    assert result == {"ok": True}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_invoke_app_missing_base_url_raises_config_error(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("AGENT_OS_BASE_URL", raising=False)
    client = agent_os.AgentOSClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(agent_os.AgentOSConfigError):
        await client.invoke_app("demo_app", {})


@pytest.mark.asyncio
async def test_invoke_app_non_object_json_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "object"])

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    with pytest.raises(agent_os.AgentOSResponseError):
        await client.invoke_app("demo_app", {})
```

- [ ] **Step 2: Run client tests to verify fail**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py -k "invoke_app" -v
```

Expected: FAIL (`AgentOSClient` missing).

- [ ] **Step 3: Implement client**

Append to `backend/app/services/agent_os.py`:

```python
import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


def _backoff_seconds(attempt: int) -> float:
    return min(2.0**attempt, 8.0)


class AgentOSClient:
    def __init__(
        self,
        *,
        settings: Optional[AgentOSSettings] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._settings = settings
        self._transport = transport

    def _resolve_settings(self) -> AgentOSSettings:
        return self._settings if self._settings is not None else load_settings()

    async def invoke_app(
        self,
        app_name: str,
        input_data: dict[str, object],
    ) -> dict[str, object]:
        settings = self._resolve_settings()
        if not settings.base_url:
            raise AgentOSConfigError(
                "AGENT_OS_BASE_URL is not configured",
                app_name=app_name,
            )
        url = f"{settings.base_url}/v1/apps/invoke"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.auth_header_name and settings.auth_header_value:
            headers[settings.auth_header_name] = settings.auth_header_value
        if settings.auth_cookie:
            headers["Cookie"] = settings.auth_cookie
        body = {"appName": app_name, "input": input_data}
        attempts = max(1, settings.max_attempts)
        last_error: Optional[Exception] = None
        timeout = httpx.Timeout(settings.timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout, transport=self._transport
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(url, json=body, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = AgentOSError(
                        f"Agent OS invoke transport error for app {app_name}: {type(exc).__name__}",
                        app_name=app_name,
                        retryable=True,
                    )
                    if attempt + 1 >= attempts:
                        raise last_error from exc
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue
                if response.status_code in _RETRYABLE_STATUS:
                    last_error = AgentOSError(
                        f"Agent OS invoke retryable HTTP {response.status_code} for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                        retryable=True,
                    )
                    if attempt + 1 >= attempts:
                        raise last_error
                    await asyncio.sleep(_backoff_seconds(attempt))
                    continue
                if response.status_code >= 400:
                    raise AgentOSError(
                        f"Agent OS invoke HTTP {response.status_code} for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                        retryable=False,
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise AgentOSResponseError(
                        f"Agent OS invoke returned non-JSON for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                    ) from exc
                if not isinstance(payload, dict):
                    raise AgentOSResponseError(
                        f"Agent OS invoke returned non-object JSON for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                    )
                return payload
        raise last_error or AgentOSError(
            f"Agent OS invoke failed for app {app_name}",
            app_name=app_name,
        )
```

Do not log request bodies, cookies, or header values.

- [ ] **Step 4: Run all client tests**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_os.py backend/tests/test_agent_os_client.py
git commit -m "$(cat <<'EOF'
feat: implement Agent OS invoke client with retries

EOF
)"
```

---

### Task 3: Explicit ChecklistCallInput context

**Files:**
- Modify: `backend/app/services/checklist_context.py`
- Modify: `backend/tests/test_checklist_context.py`
- Modify: `backend/app/services/checklist_service.py`（`input_hash` 计算）

- [ ] **Step 1: Rewrite the long-document context test**

Replace `test_long_document_builds_cache_friendly_calls_in_stable_order` in `backend/tests/test_checklist_context.py` with:

```python
def test_long_document_builds_explicit_calls_in_stable_order():
    tender = "# 第一章\n" + "甲" * 80 + "\n# 第二章\n" + "乙" * 80
    interpretation = "# 解读报告\n完整解读"
    configs = [{"title": "后项", "id": 2}, {"id": 1, "title": "前项"}]

    context = build_prompt_context(tender, interpretation, configs, 20, 14, 2)

    assert len(context.segments) > 1
    assert [call.tender_segment for call in context.calls] == context.segments
    assert context.system_instructions == checklist_context.SYSTEM_INSTRUCTIONS
    assert context.interpret_report == interpretation
    expected_config = json.dumps(
        configs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert context.admin_config == expected_config
    for index, call in enumerate(context.calls):
        assert call.system_instructions == context.system_instructions
        assert call.interpret_report == interpretation
        assert call.admin_config == expected_config
        assert call.segment_index == index
    assert tender not in context.system_instructions
```

Remove assertions on `stable_prefix` / `PromptCall.stable_prefix` elsewhere in this file.

- [ ] **Step 2: Run test to verify fail**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_checklist_context.py::test_long_document_builds_explicit_calls_in_stable_order -v
```

Expected: FAIL (missing attributes).

- [ ] **Step 3: Implement ChecklistCallInput**

In `backend/app/services/checklist_context.py`, replace `PromptCall` / `PromptContext` / `build_prompt_context` with:

```python
SYSTEM_INSTRUCTIONS = """你是招标诊断检查项生成助手。
根据解读报告、管理端配置参考与当前招标正文分片，生成本分片的近终态检查项清单。
规则：
1. 只基于本分片招标正文生成可追溯检查项；禁止无招标依据的推断。
2. 每条检查项只描述一个可独立判断的要点，填写 title/requirement/technique/importance。
3. importance 只能是 high|medium|low。
4. compliance_rules 必须包含键：satisfied, violated, cannot_satisfy, insufficient_evidence。
5. consequence_rules 的键只能来自：no_score, bid_unusable, score_risk, general_risk。
6. source_references 必须含 coordinate_space=segment、segment_index、start、end、section，且偏移落在本分片内。
7. 按预计命中的标书内容位置动态分类，输出 categories 与 items。
8. schema_version 必须为 "1"。
输出必须是符合 schema 的 JSON 对象。"""


@dataclass(frozen=True)
class ChecklistCallInput:
    system_instructions: str
    interpret_report: str
    admin_config: str
    tender_segment: str
    segment_index: int


@dataclass(frozen=True)
class PromptContext:
    system_instructions: str
    interpret_report: str
    admin_config: str
    segments: list[str]
    calls: list[ChecklistCallInput]


def build_prompt_context(
    tender_markdown: str,
    interpret_markdown: str,
    admin_configs: list[Any],
    threshold_tokens: int,
    chunk_tokens: int,
    overlap_tokens: int,
) -> PromptContext:
    segments = split_tender_markdown(
        tender_markdown,
        threshold_tokens,
        chunk_tokens,
        overlap_tokens,
    )
    config_json = json.dumps(
        admin_configs,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    calls = [
        ChecklistCallInput(
            system_instructions=SYSTEM_INSTRUCTIONS,
            interpret_report=interpret_markdown,
            admin_config=config_json,
            tender_segment=segment,
            segment_index=index,
        )
        for index, segment in enumerate(segments)
    ]
    return PromptContext(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report=interpret_markdown,
        admin_config=config_json,
        segments=segments,
        calls=calls,
    )
```

In `checklist_service.py` `_generate_locked`, change `input_hash` to:

```python
input_hash = hashlib.sha256(
    (
        context.system_instructions
        + context.interpret_report
        + context.admin_config
        + "".join(context.segments)
    ).encode("utf-8")
).hexdigest()
```

- [ ] **Step 4: Run context tests**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_checklist_context.py -v
```

Expected: PASS（若其它文件仍引用 `stable_prefix`，本 Task 可暂时只保证本文件通过；Task 7 会清掉 Mock 引用）。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/checklist_context.py backend/tests/test_checklist_context.py backend/app/services/checklist_service.py
git commit -m "$(cat <<'EOF'
feat: build checklist prompts as explicit Agent OS fields

EOF
)"
```

---

### Task 4: Cross-segment merge

**Files:**
- Create: `backend/app/engine/checklist_merge.py`
- Create: `backend/tests/test_checklist_merge.py`

- [ ] **Step 1: Write failing merge tests**

```python
from app.engine.base import ChecklistCategoryDraft, ChecklistDraft, ChecklistItemDraft
from app.engine.checklist_merge import merge_checklist_drafts


def _item(item_id, category_id, title, requirement, section="正文", segment_index=0):
    return ChecklistItemDraft(
        id=item_id,
        category_id=category_id,
        title=title,
        requirement=requirement,
        technique=f"核对{title}",
        importance="high",
        source_references=[
            {
                "section": section,
                "start": 0,
                "end": 1,
                "segment_index": segment_index,
                "coordinate_space": "segment",
            }
        ],
        retrieval_hints=[title],
        expected_evidence=[title],
        compliance_rules={
            "satisfied": "ok",
            "violated": "bad",
            "cannot_satisfy": "no",
            "insufficient_evidence": "缺少",
        },
        consequence_rules={"general_risk": "风险"},
        admin_config_refs=[],
        sort_order=1,
    )


def test_merge_dedupes_items_and_rewrites_ids():
    draft_a = ChecklistDraft(
        schema_version="1",
        categories=[
            ChecklistCategoryDraft(
                id="c-a",
                name="资格证明材料",
                description="资格",
                retrieval_query="资格",
                expected_locations=["资格"],
                sort_order=1,
            )
        ],
        items=[_item("i-a", "c-a", "营业执照", "须提供营业执照", segment_index=0)],
        raw_response={"segment": 0},
    )
    draft_b = ChecklistDraft(
        schema_version="1",
        categories=[
            ChecklistCategoryDraft(
                id="c-b",
                name="资格证明材料",
                description="应被忽略的二次描述",
                retrieval_query="证照",
                expected_locations=["证照"],
                sort_order=1,
            )
        ],
        items=[
            _item("i-b1", "c-b", "营业执照", "须提供营业执照", segment_index=1),
            _item("i-b2", "c-b", "资质证书", "须提供资质", "资质", 1),
        ],
        raw_response={"segment": 1},
    )

    merged = merge_checklist_drafts([draft_a, draft_b], max_items_per_category=20)

    assert [c.name for c in merged.categories] == ["资格证明材料"]
    assert merged.categories[0].id == "category-001"
    assert "资格" in merged.categories[0].retrieval_query
    assert "证照" in merged.categories[0].retrieval_query
    assert {item.title for item in merged.items} == {"营业执照", "资质证书"}
    assert [item.id for item in merged.items] == ["item-001", "item-002"]
    assert all(item.category_id == "category-001" for item in merged.items)
    assert "segments" in merged.raw_response
    assert "merged" in merged.raw_response


def test_merge_splits_oversized_category_by_section():
    category = ChecklistCategoryDraft(
        id="c1",
        name="综合响应材料",
        description="综合",
        retrieval_query="综合",
        expected_locations=[],
        sort_order=1,
    )
    items = [
        _item(f"i{i}", "c1", f"标题{i}", f"要求{i}", section=f"章节{i // 2}", segment_index=0)
        for i in range(5)
    ]
    draft = ChecklistDraft(
        schema_version="1",
        categories=[category],
        items=items,
        raw_response={},
    )
    merged = merge_checklist_drafts([draft], max_items_per_category=2)
    assert len(merged.categories) >= 3
    assert all(
        sum(1 for item in merged.items if item.category_id == category.id) <= 2
        for category in merged.categories
    )
```

- [ ] **Step 2: Run merge tests to fail**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_checklist_merge.py -v
```

Expected: FAIL (`checklist_merge` missing).

- [ ] **Step 3: Implement merge**

Create `backend/app/engine/checklist_merge.py`:

```python
from __future__ import annotations

import re
from typing import Any

from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)

_NORMALIZE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _NORMALIZE.sub("", value).casefold()


def merge_checklist_drafts(
    drafts: list[ChecklistDraft],
    *,
    max_items_per_category: int,
) -> ChecklistDraft:
    if not drafts:
        raise ValueError("drafts must be non-empty")

    category_by_name: dict[str, dict[str, Any]] = {}
    items: list[tuple[str, ChecklistItemDraft]] = []
    seen_items: set[tuple[str, str]] = set()
    segment_raw: list[Any] = []

    for draft in drafts:
        segment_raw.append(draft.raw_response)
        local_name = {c.id: c.name for c in draft.categories}
        for category in draft.categories:
            key = _norm(category.name)
            existing = category_by_name.get(key)
            if existing is None:
                category_by_name[key] = {
                    "name": category.name,
                    "description": category.description,
                    "retrieval_parts": [category.retrieval_query],
                    "locations": list(category.expected_locations),
                }
            else:
                if category.retrieval_query not in existing["retrieval_parts"]:
                    existing["retrieval_parts"].append(category.retrieval_query)
                for loc in category.expected_locations:
                    if loc not in existing["locations"]:
                        existing["locations"].append(loc)
        for item in draft.items:
            dedupe = (_norm(item.title), _norm(item.requirement))
            if dedupe in seen_items:
                continue
            seen_items.add(dedupe)
            items.append((local_name[item.category_id], item))

    # provisional categories by merged name
    name_order = list(dict.fromkeys(name for name, _ in items))
    for key, meta in category_by_name.items():
        if meta["name"] not in name_order:
            name_order.append(meta["name"])

    # split oversized by primary section
    buckets: dict[str, list[ChecklistItemDraft]] = {name: [] for name in name_order}
    for name, item in items:
        buckets.setdefault(name, []).append(item)

    final_categories: list[ChecklistCategoryDraft] = []
    final_items: list[ChecklistItemDraft] = []
    for base_name, bucket in buckets.items():
        meta = category_by_name[_norm(base_name)]
        if len(bucket) <= max_items_per_category:
            groups = [(base_name, bucket)]
        else:
            by_section: dict[str, list[ChecklistItemDraft]] = {}
            for item in bucket:
                section = "未标注"
                if item.source_references:
                    raw_section = item.source_references[0].get("section")
                    if isinstance(raw_section, str) and raw_section.strip():
                        section = raw_section.strip()
                by_section.setdefault(section, []).append(item)
            groups = [
                (f"{base_name}·{section}", section_items)
                for section, section_items in by_section.items()
            ]
            # if a section group still exceeds, keep it; validate_draft will fail
        for group_name, group_items in groups:
            cat_id = f"category-{len(final_categories) + 1:03d}"
            final_categories.append(
                ChecklistCategoryDraft(
                    id=cat_id,
                    name=group_name,
                    description=meta["description"],
                    retrieval_query=" ".join(meta["retrieval_parts"]),
                    expected_locations=list(meta["locations"]),
                    sort_order=len(final_categories) + 1,
                )
            )
            for item in group_items:
                final_items.append(
                    ChecklistItemDraft(
                        id=f"item-{len(final_items) + 1:03d}",
                        category_id=cat_id,
                        title=item.title,
                        requirement=item.requirement,
                        technique=item.technique,
                        importance=item.importance,
                        source_references=list(item.source_references),
                        retrieval_hints=list(item.retrieval_hints),
                        expected_evidence=list(item.expected_evidence),
                        compliance_rules=dict(item.compliance_rules),
                        consequence_rules=dict(item.consequence_rules),
                        admin_config_refs=list(item.admin_config_refs),
                        sort_order=len(final_items) + 1,
                    )
                )

    merged_payload = {
        "schema_version": drafts[0].schema_version,
        "categories": [c.__dict__ for c in final_categories],
        "items": [i.__dict__ for i in final_items],
    }
    return ChecklistDraft(
        schema_version=drafts[0].schema_version,
        categories=final_categories,
        items=final_items,
        raw_response={"segments": segment_raw, "merged": merged_payload},
    )
```

Note: `ChecklistCategoryDraft` / `ChecklistItemDraft` are frozen dataclasses — use `dataclasses.asdict` instead of `__dict__` if preferred:

```python
from dataclasses import asdict
merged_payload = {
    "schema_version": drafts[0].schema_version,
    "categories": [asdict(c) for c in final_categories],
    "items": [asdict(i) for i in final_items],
}
```

- [ ] **Step 4: Run merge tests**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_checklist_merge.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/checklist_merge.py backend/tests/test_checklist_merge.py
git commit -m "$(cat <<'EOF'
feat: merge checklist drafts across tender segments

EOF
)"
```

---

### Task 5: Publish agent via agent-create-publish + persist config

**Files:**
- Create: `docs/agents_config/tender_checklist_generator.json`
- Skill: `.cursor/skills/agent-create-publish/SKILL.md`
- Requires: `.cursor/skills/agent-create-publish/config.local.json`

**HARD GATE:** 用户确认草案前禁止 POST/PATCH/publish。

- [ ] **Step 1: Load skill config and list models (read-only)**

```bash
# parse config.local.json → BASE_URL + AUTH_ARGS per skill
curl -sS -X POST "$BASE_URL/api/v1/models/list" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d '{"page":1,"pageSize":100}'
```

- [ ] **Step 2: Present publish draft and STOP**

向用户展示完整草案（填入实际 `modelId`）：

```markdown
## 发布草案（请确认）

### 智能体
- zhName: 招标诊断检查项生成助手
- enName: tender_checklist_generator
- description: 基于解读报告、管理配置参考与招标正文分片，生成近终态诊断检查项（分类+条目），供标书合规诊断使用。

### IO
- formatInput / formatOutput: true
- inputSchema:
  - system_instructions (string, required)
  - interpret_report (string, required)
  - admin_config (string, required)
  - tender_segment (string, required)
- outputSchema:
  - schema_version (string, required)
  - categories (array<object>, required) fields: id,name,description,retrieval_query,expected_locations(array<string>),sort_order
  - items (array<object>, required) fields: id,category_id,title,requirement,technique,importance,source_references(array<object>),retrieval_hints(array<string>),expected_evidence(array<string>),compliance_rules(object),consequence_rules(object),admin_config_refs(array<number>),sort_order

### 提示词
- systemPrompt: 角色为招标诊断检查项生成助手；消费四个输入字段；只基于 tender_segment 生成可追溯检查项；输出必须严格符合 outputSchema；importance/compliance/consequence 枚举与 SYSTEM_INSTRUCTIONS 一致。
- initialMessages: []

### 模型
- modelId: <from list，优先 qwen 系列结构化能力较强者>
- temperature: 0.3
- thinking: true

### Runtime
- streaming: false
- multiTurn: false
- timeoutMs: 180000
- retryCount: 0
- showThinking: false
- sandboxEnabled: false

### 应用
- name: 招标诊断检查项生成
- enName: tender_checklist_generator_app
- mode: api
- apiConfig: { "syncType": "sync" }
- concurrency: 10
- timeoutMs: 180000
- agentVersionRef: { "publishMode": "latest" }
```

等待用户回复「确认」或修改点。

- [ ] **Step 3: After confirmation, execute skill Steps 1–7**

按 `agent-create-publish`：创建智能体 → PATCH draft（io/prompt/model/runtime）→ validate → publish agent → create application → publish application。记录 `AGENT_ID`、`APP_ID`、`publishedVersion`。

- [ ] **Step 4: Persist config snapshot**

写入 `docs/agents_config/tender_checklist_generator.json`，结构对齐 `tender_doc_interpreter.json`，至少包含 `agent`、`application`、`invoke`、`model`、`io`、`prompt`。`invoke.appName` 必须为 `tender_checklist_generator_app`，`requiredInputs` 为四个显式字段。

- [ ] **Step 5: Commit config only**

```bash
git add docs/agents_config/tender_checklist_generator.json
git commit -m "$(cat <<'EOF'
docs: persist tender_checklist_generator Agent OS config

EOF
)"
```

---

### Task 6: AgentOSChecklistAgent

**Files:**
- Create: `backend/app/engine/checklist_agent_os.py`
- Create: `backend/tests/test_checklist_agent_os.py`

- [ ] **Step 1: Write failing adapter tests**

```python
import pytest

from app.engine.checklist_agent_os import (
    TENDER_CHECKLIST_GENERATOR_APP_NAME,
    AgentOSChecklistAgent,
    ChecklistAgentResponseError,
)
from app.services.checklist_context import (
    SYSTEM_INSTRUCTIONS,
    ChecklistCallInput,
    PromptContext,
)


def _context(segment: str) -> PromptContext:
    call = ChecklistCallInput(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report="解读",
        admin_config="[]",
        tender_segment=segment,
        segment_index=0,
    )
    return PromptContext(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report="解读",
        admin_config="[]",
        segments=[segment],
        calls=[call],
    )


@pytest.mark.asyncio
async def test_generate_maps_explicit_fields_and_app_name():
    captured = {}

    async def fake_invoke(app_name, input_data):
        captured["app_name"] = app_name
        captured["input"] = input_data
        return {
            "schema_version": "1",
            "categories": [
                {
                    "id": "c1",
                    "name": "资格证明材料",
                    "description": "资格",
                    "retrieval_query": "资格",
                    "expected_locations": ["资格"],
                    "sort_order": 1,
                }
            ],
            "items": [
                {
                    "id": "i1",
                    "category_id": "c1",
                    "title": "营业执照",
                    "requirement": "须提供营业执照",
                    "technique": "核对证照",
                    "importance": "high",
                    "source_references": [
                        {
                            "section": "资格",
                            "start": 0,
                            "end": 1,
                            "segment_index": 0,
                            "coordinate_space": "segment",
                        }
                    ],
                    "retrieval_hints": ["营业执照"],
                    "expected_evidence": ["营业执照复印件"],
                    "compliance_rules": {
                        "satisfied": "有",
                        "violated": "冲突",
                        "cannot_satisfy": "不能",
                        "insufficient_evidence": "不足",
                    },
                    "consequence_rules": {"general_risk": "风险"},
                    "admin_config_refs": [],
                    "sort_order": 1,
                }
            ],
        }

    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    draft = await agent.generate(task_id="T1", context=_context("投标人须提供营业执照。"))
    assert captured["app_name"] == TENDER_CHECKLIST_GENERATOR_APP_NAME
    assert set(captured["input"]) == {
        "system_instructions",
        "interpret_report",
        "admin_config",
        "tender_segment",
    }
    assert captured["input"]["tender_segment"] == "投标人须提供营业执照。"
    assert draft.schema_version == "1"
    assert draft.categories[0].id == "category-001"
    assert draft.items[0].id == "item-001"
    assert agent.agent_type == "agent_os"
    assert agent.agent_version == "1"


@pytest.mark.asyncio
async def test_generate_rejects_missing_categories():
    async def fake_invoke(app_name, input_data):
        return {"schema_version": "1", "items": []}

    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    with pytest.raises(ChecklistAgentResponseError):
        await agent.generate(task_id="T1", context=_context("正文"))


@pytest.mark.asyncio
async def test_generate_invokes_once_per_segment():
    calls = {"n": 0}

    async def fake_invoke(app_name, input_data):
        calls["n"] += 1
        seg_index = 0 if "甲" in input_data["tender_segment"] else 1
        return {
            "schema_version": "1",
            "categories": [
                {
                    "id": "c1",
                    "name": f"分类{seg_index}",
                    "description": "d",
                    "retrieval_query": "q",
                    "expected_locations": [],
                    "sort_order": 1,
                }
            ],
            "items": [
                {
                    "id": "i1",
                    "category_id": "c1",
                    "title": f"标题{seg_index}",
                    "requirement": f"要求{seg_index}",
                    "technique": "t",
                    "importance": "medium",
                    "source_references": [
                        {
                            "section": "s",
                            "start": 0,
                            "end": 1,
                            "segment_index": seg_index,
                            "coordinate_space": "segment",
                        }
                    ],
                    "retrieval_hints": ["h"],
                    "expected_evidence": ["e"],
                    "compliance_rules": {
                        "satisfied": "a",
                        "violated": "b",
                        "cannot_satisfy": "c",
                        "insufficient_evidence": "d",
                    },
                    "consequence_rules": {"score_risk": "扣分"},
                    "admin_config_refs": [],
                    "sort_order": 1,
                }
            ],
        }

    context = PromptContext(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report="解读",
        admin_config="[]",
        segments=["甲片", "乙片"],
        calls=[
            ChecklistCallInput(
                SYSTEM_INSTRUCTIONS, "解读", "[]", "甲片", 0
            ),
            ChecklistCallInput(
                SYSTEM_INSTRUCTIONS, "解读", "[]", "乙片", 1
            ),
        ],
    )
    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    draft = await agent.generate(task_id="T2", context=context)
    assert calls["n"] == 2
    assert len(draft.items) == 2
```

- [ ] **Step 2: Run adapter tests to fail**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_checklist_agent_os.py -v
```

Expected: FAIL (module missing).

- [ ] **Step 3: Implement adapter**

Create `backend/app/engine/checklist_agent_os.py`:

```python
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Awaitable, Callable, Optional

from app import config
from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.engine.checklist_merge import merge_checklist_drafts
from app.services.agent_os import AgentOSClient
from app.services.checklist_context import PromptContext

TENDER_CHECKLIST_GENERATOR_APP_NAME = "tender_checklist_generator_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class ChecklistAgentResponseError(ValueError):
    pass


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ChecklistAgentResponseError(f"missing or empty {key}")
    return value


def parse_checklist_payload(payload: dict[str, Any]) -> ChecklistDraft:
    if not isinstance(payload, dict):
        raise ChecklistAgentResponseError("payload must be object")
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ChecklistAgentResponseError("schema_version invalid")
    categories_raw = _require_list(payload, "categories")
    items_raw = _require_list(payload, "items")
    categories: list[ChecklistCategoryDraft] = []
    for row in categories_raw:
        if not isinstance(row, dict):
            raise ChecklistAgentResponseError("category must be object")
        locations = row.get("expected_locations")
        if not isinstance(locations, list):
            raise ChecklistAgentResponseError("expected_locations must be list")
        categories.append(
            ChecklistCategoryDraft(
                id=str(row.get("id", "")),
                name=str(row.get("name", "")),
                description=str(row.get("description", "")),
                retrieval_query=str(row.get("retrieval_query", "")),
                expected_locations=[str(x) for x in locations],
                sort_order=int(row.get("sort_order", 0)),
            )
        )
    items: list[ChecklistItemDraft] = []
    for row in items_raw:
        if not isinstance(row, dict):
            raise ChecklistAgentResponseError("item must be object")
        source_references = row.get("source_references")
        retrieval_hints = row.get("retrieval_hints")
        expected_evidence = row.get("expected_evidence")
        compliance_rules = row.get("compliance_rules")
        consequence_rules = row.get("consequence_rules")
        admin_config_refs = row.get("admin_config_refs")
        if not isinstance(source_references, list):
            raise ChecklistAgentResponseError("source_references must be list")
        if not isinstance(retrieval_hints, list):
            raise ChecklistAgentResponseError("retrieval_hints must be list")
        if not isinstance(expected_evidence, list):
            raise ChecklistAgentResponseError("expected_evidence must be list")
        if not isinstance(compliance_rules, dict):
            raise ChecklistAgentResponseError("compliance_rules must be object")
        if not isinstance(consequence_rules, dict):
            raise ChecklistAgentResponseError("consequence_rules must be object")
        if not isinstance(admin_config_refs, list):
            raise ChecklistAgentResponseError("admin_config_refs must be list")
        items.append(
            ChecklistItemDraft(
                id=str(row.get("id", "")),
                category_id=str(row.get("category_id", "")),
                title=str(row.get("title", "")),
                requirement=str(row.get("requirement", "")),
                technique=str(row.get("technique", "")),
                importance=str(row.get("importance", "")),
                source_references=list(source_references),
                retrieval_hints=[str(x) for x in retrieval_hints],
                expected_evidence=[str(x) for x in expected_evidence],
                compliance_rules={str(k): str(v) for k, v in compliance_rules.items()},
                consequence_rules={str(k): str(v) for k, v in consequence_rules.items()},
                admin_config_refs=[int(x) for x in admin_config_refs],
                sort_order=int(row.get("sort_order", 0)),
            )
        )
    return ChecklistDraft(
        schema_version=schema_version,
        categories=categories,
        items=items,
        raw_response=payload,
    )


class AgentOSChecklistAgent:
    agent_type = "agent_os"
    agent_version = "1"

    def __init__(
        self,
        *,
        app_name: str = TENDER_CHECKLIST_GENERATOR_APP_NAME,
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

    async def generate(
        self,
        *,
        task_id: str,
        context: PromptContext,
    ) -> ChecklistDraft:
        del task_id
        partials: list[ChecklistDraft] = []
        for call in context.calls:
            payload = await self._invoke(
                {
                    "system_instructions": call.system_instructions,
                    "interpret_report": call.interpret_report,
                    "admin_config": call.admin_config,
                    "tender_segment": call.tender_segment,
                }
            )
            partials.append(parse_checklist_payload(payload))
        return merge_checklist_drafts(
            partials,
            max_items_per_category=config.CHECKLIST_MAX_ITEMS_PER_CATEGORY,
        )
```

- [ ] **Step 4: Run adapter tests**

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_checklist_agent_os.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/checklist_agent_os.py backend/tests/test_checklist_agent_os.py
git commit -m "$(cat <<'EOF'
feat: add Agent OS checklist agent adapter

EOF
)"
```

---

### Task 7: Wire scheduler, delete Mock, fix tests

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/app/config.py`
- Delete: `backend/app/engine/checklist_mock.py`
- Modify/Delete content: `backend/tests/test_checklist_agent.py`
- Modify: `backend/tests/test_scheduler.py`
- Modify: `backend/tests/test_checklist_api.py`
- Modify: `backend/tests/test_checklist_service.py`（若构造 `PromptContext`）

- [ ] **Step 1: Update production wiring**

In `backend/app/config.py`:

```python
CHECKLIST_AGENT = "agent_os"
CHECKLIST_AGENT_VERSION = "1"
```

In `scheduler.py`, replace Mock import/usage:

```python
from app.engine.checklist_agent_os import AgentOSChecklistAgent
# ...
await ChecklistService(agent=AgentOSChecklistAgent()).generate_for_task(task_id)
```

（两处 `MockChecklistAgent()` 均替换。）

Delete `backend/app/engine/checklist_mock.py`.

- [ ] **Step 2: Replace Mock-based tests**

Delete `backend/tests/test_checklist_agent.py`（原 Mock 行为单测；合并/适配器已覆盖）。

In `test_scheduler.py` / `test_checklist_api.py`，把阻塞/失败假 agent 的 patch 目标改为：

```python
"app.services.scheduler.AgentOSChecklistAgent"
```

假 agent 仍实现 `async def generate(self, *, task_id, context)`，可直接返回合法 `ChecklistDraft` 或抛错；不要再 import `MockChecklistAgent`。

凡构造 `PromptContext` / `PromptCall` 的测试改为 `ChecklistCallInput` 显式字段（参考 Task 6 `_context`）。

- [ ] **Step 3: Run checklist-related suite**

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_agent_os_client.py \
  tests/test_checklist_context.py \
  tests/test_checklist_merge.py \
  tests/test_checklist_agent_os.py \
  tests/test_checklist_service.py \
  tests/test_checklist_api.py \
  tests/test_scheduler.py \
  -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/scheduler.py backend/app/config.py \
  backend/tests/test_scheduler.py backend/tests/test_checklist_api.py \
  backend/tests/test_checklist_service.py
git rm -f backend/app/engine/checklist_mock.py backend/tests/test_checklist_agent.py
git commit -m "$(cat <<'EOF'
feat: switch checklist generation to Agent OS and remove mock

EOF
)"
```

---

### Task 8: Final regression

- [ ] **Step 1: Full backend pytest**

```bash
cd backend && ../.venv/bin/python -m pytest -v
```

Expected: PASS（允许跳过明确标记的外部联调用例，当前仓库默认不应有）。

- [ ] **Step 2: Grep for leftover Mock checklist**

```bash
rg -n "MockChecklistAgent|checklist_mock" backend docs/superpowers/specs/2026-07-17-agent-os-checklist-generation-design.md || true
```

Expected: 生产代码无匹配；规格文档中「删除 Mock」叙述可保留。

- [ ] **Step 3: Verify config artifact**

```bash
test -f docs/agents_config/tender_checklist_generator.json
python -c "import json; d=json.load(open('docs/agents_config/tender_checklist_generator.json')); assert d['invoke']['appName']=='tender_checklist_generator_app'; assert set(d['invoke']['requiredInputs'])=={'system_instructions','interpret_report','admin_config','tender_segment'}"
```

Expected: exit 0

- [ ] **Step 4: Commit only if Step 1–3 produced leftover fixes**

若有修复：

```bash
git add -A
git commit -m "$(cat <<'EOF'
fix: finish Agent OS checklist generation regression

EOF
)"
```

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| 发布 tender_checklist_generator api 应用 | Task 5 |
| 显式四字段输入 | Task 3, 5, 6 |
| 近终态 categories+items + 后端合并 | Task 4, 6 |
| AgentOSClient | Task 1–2 |
| 切换 scheduler / 删 Mock | Task 7 |
| docs/agents_config 持久化 | Task 5 |
| 失败不降级 / 测试用 fake transport | Task 6–8 |
| input_hash / validate 兼容 | Task 3, validate 仍用 `context.segments` |

## Placeholder / type consistency notes

- `PromptContext` 仍保留名称，避免大面积重命名；字段已无 `stable_prefix`。
- `TENDER_CHECKLIST_GENERATOR_APP_NAME` 与配置快照 `invoke.appName` 必须同为 `tender_checklist_generator_app`。
- Task 5 在用户确认前不得写 Agent OS。
