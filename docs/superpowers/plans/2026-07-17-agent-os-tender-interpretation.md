# Agent OS 招标文件解读接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 封装可复用的 Agent OS 生产态 `invoke` 客户端，并将招标文件解读步骤改为等待现有解析结果后调用已发布应用 `tender_doc_interpreter_app`。

**Architecture:** `AgentOSClient` 只负责 HTTP/鉴权/重试；`TenderContentProvider` 等待招标文件 `WorkspaceFile` 解析完成并读取 `md_path`；`AgentOSInterpretationAgent` 按应用 IO schema 映射字段；`scheduler` 编排「等待解析 → 调用智能体 → 保存报告 → 诊断」。业务应用名由适配器持有，不进全局环境变量。

**Tech Stack:** FastAPI、SQLAlchemy、asyncio、httpx（已在 `backend/requirements.txt`）、pytest-asyncio。

**Spec:** `docs/superpowers/specs/2026-07-17-agent-os-tender-interpretation-design.md`

---

## File Structure

```text
backend/app/
  config.py                         # 移除 INTERPRETATION_AGENT*；加载 Agent OS / 解析等待配置
  engine/
    base.py                         # InterpretationAgent 签名改为 tender_text + requirements
    interpretation_mock.py          # 同步更新签名（仅测试/本地残留用途）
    interpretation_agent_os.py      # NEW：AgentOSInterpretationAgent
  services/
    agent_os.py                     # NEW：settings + errors + AgentOSClient.invoke_app
    tender_content.py               # NEW：等待解析并读取招标 Markdown
    scheduler.py                    # 接线：等待正文 → AgentOSInterpretationAgent

config.local.json.example           # NEW：无凭据示例
.gitignore                          # + 根目录 config.local.json

backend/tests/
  test_agent_os_client.py           # NEW
  test_tender_content.py            # NEW
  test_interpretation_agent_os.py   # NEW
  test_interpretation_agent.py      # 更新 Mock 签名
  test_scheduler.py                 # 更新 monkeypatch 目标 + 新增顺序/停止用例
  conftest.py                       # 默认 stub 内容提供器与 Agent OS，避免假 PDF 解析拖垮全套测试
```

---

### Task 1: Agent OS 配置与错误类型

**Files:**
- Create: `backend/app/services/agent_os.py`（先放 settings/errors；client 在 Task 2）
- Modify: `backend/app/config.py`
- Create: `config.local.json.example`
- Modify: `.gitignore`
- Test: `backend/tests/test_agent_os_client.py`（本 Task 只写配置加载相关用例）

- [ ] **Step 1: Write failing tests for settings load order**

Create `backend/tests/test_agent_os_client.py`:

```python
from __future__ import annotations

import json

import pytest

from app.services import agent_os


def test_load_settings_env_overrides_local_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps(
            {
                "agentOs": {
                    "baseUrl": "http://from-json:8000",
                    "timeoutSeconds": 90,
                    "maxAttempts": 2,
                    "auth": {
                        "cookie": "c=json",
                        "headerName": "X-Token",
                        "headerValue": "json-token",
                    },
                },
                "tenderInterpretation": {"parseWaitTimeoutSeconds": 600},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", cfg)
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://from-env:9000")
    monkeypatch.setenv("AGENT_OS_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("AGENT_OS_AUTH_COOKIE", "c=env")
    monkeypatch.setenv("AGENT_OS_AUTH_HEADER_NAME", "Authorization")
    monkeypatch.setenv("AGENT_OS_AUTH_HEADER_VALUE", "Bearer env")
    monkeypatch.setenv("TENDER_PARSE_WAIT_TIMEOUT_SECONDS", "100")

    settings = agent_os.load_settings()
    assert settings.base_url == "http://from-env:9000"
    assert settings.timeout_seconds == 120
    assert settings.max_attempts == 4
    assert settings.auth_cookie == "c=env"
    assert settings.auth_header_name == "Authorization"
    assert settings.auth_header_value == "Bearer env"
    assert settings.parse_wait_timeout_seconds == 100


def test_load_settings_falls_back_to_local_json(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps({"agentOs": {"baseUrl": "http://localhost:8000"}}),
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
        "TENDER_PARSE_WAIT_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = agent_os.load_settings()
    assert settings.base_url == "http://localhost:8000"
    assert settings.timeout_seconds == 180
    assert settings.max_attempts == 3
    assert settings.parse_wait_timeout_seconds == 1800


def test_load_settings_missing_base_url_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("AGENT_OS_BASE_URL", raising=False)
    settings = agent_os.load_settings()
    assert settings.base_url == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_env_overrides_local_json tests/test_agent_os_client.py::test_load_settings_falls_back_to_local_json tests/test_agent_os_client.py::test_load_settings_missing_base_url_is_empty -v
```

Expected: FAIL with `ImportError` or `AttributeError` for `app.services.agent_os`.

- [ ] **Step 3: Implement settings, errors, and config cleanup**

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
    parse_wait_timeout_seconds: float = 1800.0


class AgentOSError(Exception):
    """Base error for Agent OS client failures."""

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
    tender = (
        local.get("tenderInterpretation")
        if isinstance(local.get("tenderInterpretation"), dict)
        else {}
    )
    auth = agent_os.get("auth") if isinstance(agent_os.get("auth"), dict) else {}

    return AgentOSSettings(
        base_url=str(_env_or(agent_os.get("baseUrl"), "AGENT_OS_BASE_URL", "")).rstrip("/"),
        timeout_seconds=_as_float(
            _env_or(agent_os.get("timeoutSeconds"), "AGENT_OS_TIMEOUT_SECONDS", 180),
            180.0,
        ),
        max_attempts=_as_int(
            _env_or(agent_os.get("maxAttempts"), "AGENT_OS_MAX_ATTEMPTS", 3),
            3,
        ),
        auth_cookie=str(_env_or(auth.get("cookie"), "AGENT_OS_AUTH_COOKIE", "")),
        auth_header_name=str(_env_or(auth.get("headerName"), "AGENT_OS_AUTH_HEADER_NAME", "")),
        auth_header_value=str(
            _env_or(auth.get("headerValue"), "AGENT_OS_AUTH_HEADER_VALUE", "")
        ),
        parse_wait_timeout_seconds=_as_float(
            _env_or(
                tender.get("parseWaitTimeoutSeconds"),
                "TENDER_PARSE_WAIT_TIMEOUT_SECONDS",
                1800,
            ),
            1800.0,
        ),
    )
```

In `backend/app/config.py`:

- Ensure `ROOT` exists (already does).
- Remove:

```python
INTERPRETATION_AGENT = "mock"  # mock | http (http not implemented this sprint)
INTERPRETATION_AGENT_URL = ""
```

- Optionally keep `MOCK_INTERPRET_DELAY_SECONDS` until Mock class is updated; leave it for now.

Create `config.local.json.example` at repo root:

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
  }
}
```

Append to `.gitignore`:

```gitignore
config.local.json
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py::test_load_settings_env_overrides_local_json tests/test_agent_os_client.py::test_load_settings_falls_back_to_local_json tests/test_agent_os_client.py::test_load_settings_missing_base_url_is_empty -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_os.py backend/app/config.py backend/tests/test_agent_os_client.py config.local.json.example .gitignore
git commit -m "$(cat <<'EOF'
feat: add Agent OS settings loader and config example

EOF
)"
```

---

### Task 2: AgentOSClient.invoke_app

**Files:**
- Modify: `backend/app/services/agent_os.py`
- Modify: `backend/tests/test_agent_os_client.py`

- [ ] **Step 1: Write failing client tests**

Append to `backend/tests/test_agent_os_client.py`:

```python
import httpx
import pytest


@pytest.mark.asyncio
async def test_invoke_app_posts_camel_case_body(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "1")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content.decode("utf-8"))
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"report_markdown": "# ok\n", "extra": 1})

    transport = httpx.MockTransport(handler)
    client = agent_os.AgentOSClient(transport=transport)
    result = await client.invoke_app(
        "demo_app",
        {"tender_text": "正文", "project_background": "bg"},
    )
    assert result["report_markdown"] == "# ok\n"
    assert captured["url"] == "http://agent-os.test/v1/apps/invoke"
    assert captured["json"] == {
        "appName": "demo_app",
        "input": {"tender_text": "正文", "project_background": "bg"},
    }


@pytest.mark.asyncio
async def test_invoke_app_sends_auth_cookie_and_header(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    monkeypatch.setenv("AGENT_OS_AUTH_COOKIE", "sid=abc")
    monkeypatch.setenv("AGENT_OS_AUTH_HEADER_NAME", "X-Api-Key")
    monkeypatch.setenv("AGENT_OS_AUTH_HEADER_VALUE", "secret")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "1")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie")
        seen["x"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"ok": True})

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    await client.invoke_app("demo_app", {"a": 1})
    assert seen["cookie"] == "sid=abc"
    assert seen["x"] == "secret"


@pytest.mark.asyncio
async def test_invoke_app_retries_502_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(agent_os, "_backoff_seconds", lambda attempt: 0)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502, json={"error": "bad gateway"})
        return httpx.Response(200, json={"report_markdown": "done"})

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    result = await client.invoke_app("demo_app", {"tender_text": "x"})
    assert result["report_markdown"] == "done"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_invoke_app_does_not_retry_400(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "3")
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    with pytest.raises(agent_os.AgentOSError) as exc:
        await client.invoke_app("demo_app", {"tender_text": "x"})
    assert calls["n"] == 1
    assert exc.value.status_code == 400
    assert exc.value.retryable is False
    assert "demo_app" in str(exc.value)
    assert "x" not in str(exc.value)  # no input body leak


@pytest.mark.asyncio
async def test_invoke_app_requires_base_url(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.delenv("AGENT_OS_BASE_URL", raising=False)
    client = agent_os.AgentOSClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with pytest.raises(agent_os.AgentOSConfigError):
        await client.invoke_app("demo_app", {"tender_text": "x"})


@pytest.mark.asyncio
async def test_invoke_app_rejects_non_object_json(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "object"])

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    with pytest.raises(agent_os.AgentOSResponseError):
        await client.invoke_app("demo_app", {"tender_text": "x"})
```

- [ ] **Step 2: Run new tests to verify they fail**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_client.py -k "invoke_app" -v
```

Expected: FAIL with `AttributeError: AgentOSClient`.

- [ ] **Step 3: Implement AgentOSClient**

Append to `backend/app/services/agent_os.py`:

```python
import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


def _backoff_seconds(attempt: int) -> float:
    # attempt is 0-based index of the failure just observed
    return min(2.0 ** attempt, 8.0)


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
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
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


async def invoke_app(app_name: str, input_data: dict[str, object]) -> dict[str, object]:
    return await AgentOSClient().invoke_app(app_name, input_data)
```

Do not log request bodies, cookies, or header values.

- [ ] **Step 4: Run client tests**

Run:

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

### Task 3: TenderContentProvider

**Files:**
- Create: `backend/app/services/tender_content.py`
- Create: `backend/tests/test_tender_content.py`

- [ ] **Step 1: Write failing provider tests**

Create `backend/tests/test_tender_content.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, DiagnosisTask, WorkspaceFile, utcnow
from app.services import tender_content


@pytest_asyncio.fixture
async def session_factory(tmp_path, monkeypatch):
    db_path = tmp_path / "tc.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr("app.db.SessionLocal", session_factory)
    monkeypatch.setattr(tender_content, "POLL_INTERVAL_SECONDS", 0.01)
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_wait_reads_succeeded_markdown(tmp_path, session_factory):
    md = tmp_path / "tender.md"
    md.write_text("# 招标全文\n", encoding="utf-8")
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="task1",
                tender_filename="t.pdf",
                tender_path="/x/t.pdf",
                bid_filename="b.docx",
                bid_path="/x/b.docx",
                tender_file_id="tf1",
                config_snapshot="[]",
                status="interpreting",
            )
        )
        session.add(
            WorkspaceFile(
                id="tf1",
                task_id="task1",
                label="招标文件",
                original_filename="t.pdf",
                stored_path="/x/t.pdf",
                kind="document",
                ext=".pdf",
                parse_status="succeeded",
                md_path=str(md),
            )
        )
        await session.commit()

    text = await tender_content.wait_for_tender_text(
        task_id="task1",
        tender_file_id="tf1",
        should_stop=lambda: False,
        timeout_seconds=1.0,
    )
    assert text == "# 招标全文\n"


@pytest.mark.asyncio
async def test_wait_accepts_partial(tmp_path, session_factory):
    md = tmp_path / "tender.md"
    md.write_text("partial body", encoding="utf-8")
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="task1",
                tender_filename="t.pdf",
                tender_path="/x/t.pdf",
                bid_filename="b.docx",
                bid_path="/x/b.docx",
                tender_file_id="tf1",
                config_snapshot="[]",
            )
        )
        session.add(
            WorkspaceFile(
                id="tf1",
                task_id="task1",
                label="招标文件",
                original_filename="t.pdf",
                stored_path="/x/t.pdf",
                kind="document",
                ext=".pdf",
                parse_status="partial",
                md_path=str(md),
            )
        )
        await session.commit()

    text = await tender_content.wait_for_tender_text(
        task_id="task1",
        tender_file_id="tf1",
        should_stop=lambda: False,
        timeout_seconds=1.0,
    )
    assert text == "partial body"


@pytest.mark.asyncio
async def test_wait_polls_until_succeeded(tmp_path, session_factory):
    md = tmp_path / "tender.md"
    md.write_text("later", encoding="utf-8")
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="task1",
                tender_filename="t.pdf",
                tender_path="/x/t.pdf",
                bid_filename="b.docx",
                bid_path="/x/b.docx",
                tender_file_id="tf1",
                config_snapshot="[]",
            )
        )
        session.add(
            WorkspaceFile(
                id="tf1",
                task_id="task1",
                label="招标文件",
                original_filename="t.pdf",
                stored_path="/x/t.pdf",
                kind="document",
                ext=".pdf",
                parse_status="pending",
                md_path=None,
            )
        )
        await session.commit()

    async def flip():
        await asyncio.sleep(0.03)
        async with session_factory() as session:
            wf = await session.get(WorkspaceFile, "tf1")
            wf.parse_status = "succeeded"
            wf.md_path = str(md)
            wf.updated_at = utcnow()
            await session.commit()

    asyncio.create_task(flip())
    text = await tender_content.wait_for_tender_text(
        task_id="task1",
        tender_file_id="tf1",
        should_stop=lambda: False,
        timeout_seconds=2.0,
    )
    assert text == "later"


@pytest.mark.asyncio
async def test_wait_failed_parse_raises(session_factory):
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="task1",
                tender_filename="t.pdf",
                tender_path="/x/t.pdf",
                bid_filename="b.docx",
                bid_path="/x/b.docx",
                tender_file_id="tf1",
                config_snapshot="[]",
            )
        )
        session.add(
            WorkspaceFile(
                id="tf1",
                task_id="task1",
                label="招标文件",
                original_filename="t.pdf",
                stored_path="/x/t.pdf",
                kind="document",
                ext=".pdf",
                parse_status="failed",
                parse_error="convert_failed",
            )
        )
        await session.commit()

    with pytest.raises(tender_content.TenderContentError, match="parse_failed"):
        await tender_content.wait_for_tender_text(
            task_id="task1",
            tender_file_id="tf1",
            should_stop=lambda: False,
            timeout_seconds=0.5,
        )


@pytest.mark.asyncio
async def test_wait_stop_raises_stopped(session_factory):
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="task1",
                tender_filename="t.pdf",
                tender_path="/x/t.pdf",
                bid_filename="b.docx",
                bid_path="/x/b.docx",
                tender_file_id="tf1",
                config_snapshot="[]",
            )
        )
        session.add(
            WorkspaceFile(
                id="tf1",
                task_id="task1",
                label="招标文件",
                original_filename="t.pdf",
                stored_path="/x/t.pdf",
                kind="document",
                ext=".pdf",
                parse_status="running",
            )
        )
        await session.commit()

    with pytest.raises(tender_content.TenderContentStopped):
        await tender_content.wait_for_tender_text(
            task_id="task1",
            tender_file_id="tf1",
            should_stop=lambda: True,
            timeout_seconds=1.0,
        )


@pytest.mark.asyncio
async def test_wait_timeout(session_factory):
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id="task1",
                tender_filename="t.pdf",
                tender_path="/x/t.pdf",
                bid_filename="b.docx",
                bid_path="/x/b.docx",
                tender_file_id="tf1",
                config_snapshot="[]",
            )
        )
        session.add(
            WorkspaceFile(
                id="tf1",
                task_id="task1",
                label="招标文件",
                original_filename="t.pdf",
                stored_path="/x/t.pdf",
                kind="document",
                ext=".pdf",
                parse_status="pending",
            )
        )
        await session.commit()

    with pytest.raises(tender_content.TenderContentError, match="timeout"):
        await tender_content.wait_for_tender_text(
            task_id="task1",
            tender_file_id="tf1",
            should_stop=lambda: False,
            timeout_seconds=0.05,
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_tender_content.py -v
```

Expected: FAIL with import error for `tender_content`.

- [ ] **Step 3: Implement provider**

Create `backend/app/services/tender_content.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from app import db as database
from app.models import WorkspaceFile
from app.services.agent_os import load_settings

POLL_INTERVAL_SECONDS = 0.5
_READY = frozenset({"succeeded", "partial"})
_FAILED = frozenset({"failed"})
_PENDING = frozenset({"pending", "running"})


class TenderContentError(Exception):
    pass


class TenderContentStopped(Exception):
    """Raised when the task stop flag is observed while waiting for parse."""


async def wait_for_tender_text(
    *,
    task_id: str,
    tender_file_id: str,
    should_stop: Callable[[], bool],
    timeout_seconds: float | None = None,
) -> str:
    if not tender_file_id:
        raise TenderContentError("tender_file_id_missing")

    settings = load_settings()
    deadline = asyncio.get_running_loop().time() + (
        settings.parse_wait_timeout_seconds if timeout_seconds is None else timeout_seconds
    )

    while True:
        if should_stop():
            raise TenderContentStopped()

        async with database.SessionLocal() as session:
            wf = await session.get(WorkspaceFile, tender_file_id)
            if wf is None or wf.task_id != task_id:
                raise TenderContentError("workspace_file_not_found")
            status = wf.parse_status
            md_path = wf.md_path

        if status in _FAILED:
            raise TenderContentError("parse_failed")
        if status in _READY:
            if not md_path:
                raise TenderContentError("md_path_missing")
            path = Path(md_path)
            if not path.is_file():
                raise TenderContentError("md_file_missing")
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise TenderContentError("md_empty")
            return text
        if status not in _PENDING:
            raise TenderContentError(f"unexpected_parse_status:{status}")

        if asyncio.get_running_loop().time() >= deadline:
            raise TenderContentError("timeout")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        if should_stop():
            raise TenderContentStopped()
```

- [ ] **Step 4: Run provider tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_tender_content.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tender_content.py backend/tests/test_tender_content.py
git commit -m "$(cat <<'EOF'
feat: wait for parsed tender markdown before interpretation

EOF
)"
```

---

### Task 4: Update InterpretationAgent protocol + Mock

**Files:**
- Modify: `backend/app/engine/base.py`
- Modify: `backend/app/engine/interpretation_mock.py`
- Modify: `backend/tests/test_interpretation_agent.py`

- [ ] **Step 1: Write failing protocol/mock test update**

Replace `backend/tests/test_interpretation_agent.py` with:

```python
import pytest

from app.engine.interpretation_mock import MockInterpretationAgent


@pytest.mark.asyncio
async def test_mock_interpretation_returns_markdown_with_sections():
    agent = MockInterpretationAgent(delay_seconds=0)
    result = await agent.interpret(
        task_id="T-20260716-001",
        tender_text="# 招标文件\n正文",
        background="市政工程",
        requirements="关注废标条款",
    )
    assert result.title == "招标文件解读报告"
    assert "# 招标文件解读报告" in result.markdown
    for heading in (
        "项目概况",
        "招标范围与资质要求",
        "评分办法要点",
        "废标/否决条款摘要",
        "风险提示",
    ):
        assert heading in result.markdown
    assert "市政工程" in result.markdown
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent.py -v
```

Expected: FAIL on unexpected keyword `tender_text` / missing `tender_path`.

- [ ] **Step 3: Update protocol and mock**

In `backend/app/engine/base.py`, replace `InterpretationAgent` with:

```python
class InterpretationAgent(Protocol):
    async def interpret(
        self,
        *,
        task_id: str,
        tender_text: str,
        background: str,
        requirements: str,
    ) -> InterpretationResult: ...
```

Update `backend/app/engine/interpretation_mock.py` `interpret` to accept `tender_text` / `requirements` instead of `tender_path`. Use a short excerpt of `tender_text` (first 40 chars) in the mock markdown instead of filename. Keep title `招标文件解读报告`.

- [ ] **Step 4: Run mock test**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/base.py backend/app/engine/interpretation_mock.py backend/tests/test_interpretation_agent.py
git commit -m "$(cat <<'EOF'
refactor: pass parsed tender text into InterpretationAgent

EOF
)"
```

---

### Task 5: AgentOSInterpretationAgent

**Files:**
- Create: `backend/app/engine/interpretation_agent_os.py`
- Create: `backend/tests/test_interpretation_agent_os.py`

- [ ] **Step 1: Write failing adapter tests**

Create `backend/tests/test_interpretation_agent_os.py`:

```python
from __future__ import annotations

import pytest

from app.engine.interpretation_agent_os import (
    TENDER_DOC_INTERPRETER_APP_NAME,
    AgentOSInterpretationAgent,
)


@pytest.mark.asyncio
async def test_maps_fields_and_reads_report_markdown():
    captured: dict = {}

    class FakeClient:
        async def invoke_app(self, app_name, input_data):
            captured["app_name"] = app_name
            captured["input"] = input_data
            return {"report_markdown": "# 解读\n", "project_basic_info": {}}

    agent = AgentOSInterpretationAgent(client=FakeClient())
    result = await agent.interpret(
        task_id="t1",
        tender_text="全文",
        background="背景",
        requirements="要求",
    )
    assert captured["app_name"] == TENDER_DOC_INTERPRETER_APP_NAME
    assert captured["app_name"] == "tender_doc_interpreter_app"
    assert captured["input"] == {
        "tender_text": "全文",
        "project_background": "背景",
        "interpretation_requirements": "要求",
    }
    assert result.markdown == "# 解读\n"
    assert result.title == "招标文件解读报告"


@pytest.mark.asyncio
async def test_rejects_empty_report_markdown():
    class FakeClient:
        async def invoke_app(self, app_name, input_data):
            return {"report_markdown": "  "}

    agent = AgentOSInterpretationAgent(client=FakeClient())
    with pytest.raises(ValueError, match="report_markdown"):
        await agent.interpret(
            task_id="t1",
            tender_text="全文",
            background="",
            requirements="",
        )


@pytest.mark.asyncio
async def test_rejects_missing_report_markdown():
    class FakeClient:
        async def invoke_app(self, app_name, input_data):
            return {"markdown": "# wrong field"}

    agent = AgentOSInterpretationAgent(client=FakeClient())
    with pytest.raises(ValueError, match="report_markdown"):
        await agent.interpret(
            task_id="t1",
            tender_text="全文",
            background="",
            requirements="",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent_os.py -v
```

Expected: FAIL with import error.

- [ ] **Step 3: Implement adapter**

Create `backend/app/engine/interpretation_agent_os.py`:

```python
from __future__ import annotations

from typing import Any, Optional, Protocol

from app.engine.base import InterpretationResult
from app.services.agent_os import AgentOSClient

TENDER_DOC_INTERPRETER_APP_NAME = "tender_doc_interpreter_app"


class _InvokeClient(Protocol):
    async def invoke_app(
        self,
        app_name: str,
        input_data: dict[str, object],
    ) -> dict[str, object]: ...


class AgentOSInterpretationAgent:
    def __init__(
        self,
        *,
        client: Optional[_InvokeClient] = None,
        app_name: str = TENDER_DOC_INTERPRETER_APP_NAME,
    ) -> None:
        self._client: _InvokeClient = client or AgentOSClient()
        self._app_name = app_name

    async def interpret(
        self,
        *,
        task_id: str,
        tender_text: str,
        background: str,
        requirements: str,
    ) -> InterpretationResult:
        if not tender_text.strip():
            raise ValueError("tender_text is empty")

        payload = await self._client.invoke_app(
            self._app_name,
            {
                "tender_text": tender_text,
                "project_background": background or "",
                "interpretation_requirements": requirements or "",
            },
        )
        report = payload.get("report_markdown")
        if not isinstance(report, str) or not report.strip():
            raise ValueError("report_markdown missing or empty")
        return InterpretationResult(markdown=report, title="招标文件解读报告")
```

- [ ] **Step 4: Run adapter tests**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent_os.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/engine/interpretation_agent_os.py backend/tests/test_interpretation_agent_os.py
git commit -m "$(cat <<'EOF'
feat: add Agent OS interpretation adapter for tender_doc_interpreter_app

EOF
)"
```

---

### Task 6: Wire scheduler + stabilize API tests via conftest stubs

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write/adjust failing scheduler-oriented expectations**

In `backend/tests/conftest.py`, after shortening mock delays, add default stubs so fake PDF tasks still complete without real Agent OS / real parse wait:

```python
    async def _stub_wait_for_tender_text(**_kwargs):
        return "# stub tender markdown\n"

    class _StubInterpretationAgent:
        def __init__(self, *args, **kwargs):
            pass

        async def interpret(self, **kwargs):
            from app.engine.base import InterpretationResult

            return InterpretationResult(
                markdown="# 招标文件解读报告\n\nstub from conftest\n",
                title="招标文件解读报告",
            )

    monkeypatch.setattr(
        "app.services.scheduler.wait_for_tender_text",
        _stub_wait_for_tender_text,
    )
    monkeypatch.setattr(
        "app.services.scheduler.AgentOSInterpretationAgent",
        _StubInterpretationAgent,
    )
```

Update `backend/tests/test_scheduler.py` monkeypatches that currently target `MockInterpretationAgent.interpret` to target `AgentOSInterpretationAgent.interpret` instead (same module path after wiring: `app.services.scheduler.AgentOSInterpretationAgent.interpret`).

Add new test at end of `test_scheduler.py`:

```python
@pytest.mark.asyncio
async def test_interpret_uses_waited_text_and_requirements(client, monkeypatch):
    seen: dict = {}

    async def capture_wait(**kwargs):
        seen["wait"] = kwargs
        return "REAL_TENDER_TEXT"

    class CapturingAgent:
        def __init__(self, *args, **kwargs):
            pass

        async def interpret(self, **kwargs):
            seen["interpret"] = kwargs
            from app.engine.base import InterpretationResult

            return InterpretationResult(markdown="# report\n")

    monkeypatch.setattr("app.services.scheduler.wait_for_tender_text", capture_wait)
    monkeypatch.setattr("app.services.scheduler.AgentOSInterpretationAgent", CapturingAgent)

    await _seed_configs(client, 1)
    body = await _create_task(client)
    status = await scheduler.wait_for_terminal(body["id"], timeout=5)
    assert status == "completed"
    assert seen["wait"]["task_id"] == body["id"]
    assert seen["wait"]["tender_file_id"]
    assert seen["interpret"]["tender_text"] == "REAL_TENDER_TEXT"
    assert seen["interpret"]["background"] == "bg"
    assert seen["interpret"]["requirements"] == "req"


@pytest.mark.asyncio
async def test_stop_discards_late_interpret_result(client, monkeypatch):
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_wait(**_kwargs):
        started.set()
        await release.wait()
        return "late-text"

    class LateAgent:
        def __init__(self, *args, **kwargs):
            pass

        async def interpret(self, **kwargs):
            from app.engine.base import InterpretationResult

            return InterpretationResult(markdown="# should-not-save\n")

    monkeypatch.setattr("app.services.scheduler.wait_for_tender_text", slow_wait)
    monkeypatch.setattr("app.services.scheduler.AgentOSInterpretationAgent", LateAgent)

    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]
    for _ in range(100):
        if started.is_set():
            break
        await asyncio.sleep(0.02)
    assert started.is_set()

    r = await client.post(f"/api/tasks/{task_id}/stop")
    assert r.status_code == 200
    release.set()
    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert not detail.get("interpret_md_path")
    assert detail.get("results") == []
```

Also update failure test to monkeypatch `AgentOSInterpretationAgent.interpret`.

- [ ] **Step 2: Run scheduler tests to see current failures**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_scheduler.py -v
```

Expected: FAIL because scheduler still imports/uses `MockInterpretationAgent` and does not call `wait_for_tender_text`.

- [ ] **Step 3: Wire scheduler**

In `backend/app/services/scheduler.py`:

1. Replace imports:

```python
from app.engine.interpretation_agent_os import AgentOSInterpretationAgent
from app.services.tender_content import TenderContentStopped, wait_for_tender_text
```

Remove `MockInterpretationAgent` and `MOCK_INTERPRET_DELAY_SECONDS` imports if unused.

2. In `_run`, when reading task fields also load:

```python
            requirements = task.requirements or ""
            tender_file_id = task.tender_file_id or ""
```

3. Replace interpret block with:

```python
        if need_interpret:
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            try:
                tender_text = await wait_for_tender_text(
                    task_id=task_id,
                    tender_file_id=tender_file_id,
                    should_stop=lambda: _should_stop(task_id),
                )
            except TenderContentStopped:
                await _mark_stopped(task_id)
                return

            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            agent = AgentOSInterpretationAgent()
            interpret_result = await agent.interpret(
                task_id=task_id,
                tender_text=tender_text,
                background=background,
                requirements=requirements,
            )
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            # Re-check DB status before persisting (stop may have won the race)
            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                if task is None:
                    return
                if task.status in TERMINAL_STATUSES:
                    return
                if task.status == "stopped" or _should_stop(task_id):
                    await _mark_stopped(task_id)
                    return

            md_path, html_path = interpret_report.save_interpret_reports(
                task_id, interpret_result
            )

            async with database.SessionLocal() as session:
                task = await session.get(DiagnosisTask, task_id)
                if task is None:
                    return
                if task.status in TERMINAL_STATUSES:
                    return
                if _should_stop(task_id):
                    await _mark_stopped(task_id)
                    return
                task.interpret_md_path = md_path
                task.interpret_html_path = html_path
                task.status = "diagnosing"
                task.updated_at = utcnow()
                await session.commit()

            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return
```

Keep the existing outer `except Exception` that marks `failed` for parse/agent errors. `TenderContentStopped` must not become `failed`.

- [ ] **Step 4: Run scheduler + related suites**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_scheduler.py tests/test_report.py tests/test_tasks.py tests/test_interpretation_agent.py tests/test_interpretation_agent_os.py tests/test_tender_content.py tests/test_agent_os_client.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduler.py backend/tests/conftest.py backend/tests/test_scheduler.py
git commit -m "$(cat <<'EOF'
feat: run Agent OS interpretation after tender parse completes

EOF
)"
```

---

### Task 7: Full regression + docs touch-up

**Files:**
- Possibly modify: any remaining references to `INTERPRETATION_AGENT` / `MockInterpretationAgent` in scheduler tests or docs
- No production API/schema changes expected

- [ ] **Step 1: Search for stale references**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application && rg -n "INTERPRETATION_AGENT|MockInterpretationAgent|INTERPRETATION_AGENT_URL" backend docs/superpowers/plans/2026-07-17-agent-os-tender-interpretation.md
```

Fix any remaining production references. Keep Mock class for unit tests only.

- [ ] **Step 2: Run full backend test suite**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest -v
```

Expected: PASS (all green)

- [ ] **Step 3: Commit any leftover fixes**

```bash
git add -A
git status
# only if there are relevant fixes:
git commit -m "$(cat <<'EOF'
test: finish Agent OS interpretation integration cleanup

EOF
)"
```

---

## Self-Review Checklist

| Spec requirement | Task |
|---|---|
| 通用 `/v1/apps/invoke` 客户端 | Task 1–2 |
| `appName` 每次调用传入，非全局 env | Task 2 + Task 5 |
| 等待解析成功/部分成功后读真实 Markdown | Task 3 + Task 6 |
| IO 字段 `tender_text` / `project_background` / `interpretation_requirements` | Task 5 |
| 输出 `report_markdown` → 现有报告链路 | Task 5–6 |
| 有限重试后失败，无 Mock 降级 | Task 2 + Task 6 |
| 停止丢弃迟到结果 | Task 6 |
| 默认测试不依赖真实 Agent OS | Task 6 conftest stubs + MockTransport |
| 不实现 chat/aichat/runtime/片段检索 | 全计划均未纳入 |

Placeholder scan: none intentional. Type names locked as `AgentOSClient.invoke_app`, `wait_for_tender_text`, `AgentOSInterpretationAgent`, `TENDER_DOC_INTERPRETER_APP_NAME`.
