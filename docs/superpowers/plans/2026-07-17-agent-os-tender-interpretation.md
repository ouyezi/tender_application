# Agent OS 招标文件解读接入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 封装可复用的 Agent OS `/v1/apps/invoke` 客户端，并让招标文件解读使用现有解析流程产生的真实全文调用 `tender_doc_interpreter_app`。

**Architecture:** 通用配置加载器和 `AgentOSClient` 只负责 Agent OS 传输协议；`TenderContentProvider` 负责等待并读取招标文件 Markdown；`AgentOSInterpretationAgent` 负责业务字段映射；现有 scheduler 仅编排“解析 → 解读 → 诊断”。所有默认测试使用 HTTP/业务替身，不依赖外部 Agent OS。

**Tech Stack:** Python 3.11+、FastAPI、SQLAlchemy AsyncSession、httpx、pytest、pytest-asyncio

**Design reference:** `docs/superpowers/specs/2026-07-17-agent-os-tender-interpretation-design.md`

---

## 文件结构

新增：

- `backend/app/services/agent_os_config.py`：加载公共 Agent OS 连接配置和招标文件解析等待配置。
- `backend/app/services/agent_os.py`：可复用的生产态 `/v1/apps/invoke` HTTP 客户端。
- `backend/app/services/tender_content.py`：等待 `WorkspaceFile` 解析并读取真实 Markdown。
- `backend/app/engine/interpretation_agent_os.py`：`tender_doc_interpreter_app` 业务适配器。
- `backend/tests/test_agent_os_config.py`：配置优先级和校验测试。
- `backend/tests/test_agent_os.py`：请求、鉴权、响应和重试测试。
- `backend/tests/test_tender_content.py`：解析等待、读取、失败和停止测试。
- `backend/tests/test_interpretation_agent_os.py`：业务输入输出映射测试。
- `config.example.json`：不含凭据的运行时配置示例。

修改：

- `backend/app/engine/base.py`：将解读协议改为接收真实正文和 requirements。
- `backend/app/services/scheduler.py`：接入内容提供器和 Agent OS 解读适配器。
- `backend/tests/conftest.py`：为默认 API 测试注入离线解读替身。
- `backend/tests/test_scheduler.py`：改为验证真实正文映射、失败和迟到响应。
- `backend/app/config.py`：删除失效的 Mock/HTTP 选择配置。
- `.gitignore`：忽略项目根目录 `config.local.json`，保留用户已有修改。

删除：

- `backend/app/engine/interpretation_mock.py`
- `backend/tests/test_interpretation_agent.py`

---

### Task 1: 运行时配置加载器

**Files:**
- Create: `backend/app/services/agent_os_config.py`
- Create: `backend/tests/test_agent_os_config.py`
- Create: `config.example.json`
- Modify: `.gitignore`

- [ ] **Step 1: 编写配置优先级失败测试**

创建 `backend/tests/test_agent_os_config.py`，覆盖 JSON 回退、环境变量覆盖和非法数值：

```python
import json

import pytest

from app.services.agent_os_config import (
    AgentOSConfigurationError,
    load_agent_os_settings,
    load_tender_content_settings,
)


_ENV_KEYS = (
    "AGENT_OS_BASE_URL",
    "AGENT_OS_TIMEOUT_SECONDS",
    "AGENT_OS_MAX_ATTEMPTS",
    "AGENT_OS_AUTH_COOKIE",
    "AGENT_OS_AUTH_HEADER_NAME",
    "AGENT_OS_AUTH_HEADER_VALUE",
    "TENDER_PARSE_WAIT_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def clear_agent_os_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_loads_json_fallback_and_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "config.local.json"
    path.write_text(
        json.dumps(
            {
                "agentOs": {
                    "baseUrl": "http://json-agent-os:8000/",
                    "timeoutSeconds": 180,
                    "maxAttempts": 3,
                    "auth": {
                        "cookie": "json-cookie",
                        "headerName": "X-Agent-Key",
                        "headerValue": "json-secret",
                    },
                },
                "tenderInterpretation": {"parseWaitTimeoutSeconds": 1800},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://env-agent-os:9000/")
    monkeypatch.setenv("AGENT_OS_MAX_ATTEMPTS", "4")

    agent = load_agent_os_settings(path)
    tender = load_tender_content_settings(path)

    assert agent.base_url == "http://env-agent-os:9000"
    assert agent.timeout_seconds == 180
    assert agent.max_attempts == 4
    assert agent.cookie == "json-cookie"
    assert agent.header_name == "X-Agent-Key"
    assert agent.header_value == "json-secret"
    assert tender.parse_wait_timeout_seconds == 1800


def test_missing_file_uses_non_secret_defaults(tmp_path):
    agent = load_agent_os_settings(tmp_path / "missing.json")
    tender = load_tender_content_settings(tmp_path / "missing.json")

    assert agent.base_url == ""
    assert agent.timeout_seconds == 180
    assert agent.max_attempts == 3
    assert tender.parse_wait_timeout_seconds == 1800


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AGENT_OS_TIMEOUT_SECONDS", "0"),
        ("AGENT_OS_MAX_ATTEMPTS", "0"),
        ("TENDER_PARSE_WAIT_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_rejects_non_positive_numeric_settings(tmp_path, monkeypatch, key, value):
    monkeypatch.setenv(key, value)

    with pytest.raises(AgentOSConfigurationError):
        if key == "TENDER_PARSE_WAIT_TIMEOUT_SECONDS":
            load_tender_content_settings(tmp_path / "missing.json")
        else:
            load_agent_os_settings(tmp_path / "missing.json")
```

- [ ] **Step 2: 运行测试并确认因模块缺失而失败**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_config.py -q
```

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'app.services.agent_os_config'`。

- [ ] **Step 3: 实现最小配置加载器**

创建 `backend/app/services/agent_os_config.py`：

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import ROOT


class AgentOSConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AgentOSSettings:
    base_url: str
    timeout_seconds: float
    max_attempts: int
    cookie: str
    header_name: str
    header_value: str


@dataclass(frozen=True)
class TenderContentSettings:
    parse_wait_timeout_seconds: float


def _read_config(path: Path | None) -> dict[str, Any]:
    resolved = path or (ROOT / "config.local.json")
    if not resolved.is_file():
        return {}
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentOSConfigurationError(f"invalid config file: {resolved}") from exc
    if not isinstance(value, dict):
        raise AgentOSConfigurationError("config.local.json must contain a JSON object")
    return value


def _positive_float(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AgentOSConfigurationError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise AgentOSConfigurationError(f"{name} must be greater than zero")
    return parsed


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AgentOSConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise AgentOSConfigurationError(f"{name} must be greater than zero")
    return parsed


def load_agent_os_settings(path: Path | None = None) -> AgentOSSettings:
    root = _read_config(path)
    block = root.get("agentOs") if isinstance(root.get("agentOs"), dict) else {}
    auth = block.get("auth") if isinstance(block.get("auth"), dict) else {}
    base_url = os.getenv("AGENT_OS_BASE_URL", str(block.get("baseUrl", ""))).rstrip("/")
    timeout = os.getenv(
        "AGENT_OS_TIMEOUT_SECONDS", str(block.get("timeoutSeconds", 180))
    )
    attempts = os.getenv(
        "AGENT_OS_MAX_ATTEMPTS", str(block.get("maxAttempts", 3))
    )
    return AgentOSSettings(
        base_url=base_url,
        timeout_seconds=_positive_float(timeout, "AGENT_OS_TIMEOUT_SECONDS"),
        max_attempts=_positive_int(attempts, "AGENT_OS_MAX_ATTEMPTS"),
        cookie=os.getenv("AGENT_OS_AUTH_COOKIE", str(auth.get("cookie", ""))),
        header_name=os.getenv(
            "AGENT_OS_AUTH_HEADER_NAME", str(auth.get("headerName", ""))
        ),
        header_value=os.getenv(
            "AGENT_OS_AUTH_HEADER_VALUE", str(auth.get("headerValue", ""))
        ),
    )


def load_tender_content_settings(
    path: Path | None = None,
) -> TenderContentSettings:
    root = _read_config(path)
    block = (
        root.get("tenderInterpretation")
        if isinstance(root.get("tenderInterpretation"), dict)
        else {}
    )
    timeout = os.getenv(
        "TENDER_PARSE_WAIT_TIMEOUT_SECONDS",
        str(block.get("parseWaitTimeoutSeconds", 1800)),
    )
    return TenderContentSettings(
        parse_wait_timeout_seconds=_positive_float(
            timeout, "TENDER_PARSE_WAIT_TIMEOUT_SECONDS"
        )
    )
```

- [ ] **Step 4: 增加安全的配置示例和忽略规则**

创建 `config.example.json`：

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

在 `.gitignore` 末尾追加以下规则；不要覆盖或回退用户已经存在的修改：

```gitignore

# Runtime Agent OS config (may contain auth)
/config.local.json
```

- [ ] **Step 5: 运行配置测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os_config.py -q
```

Expected: `5 passed`（参数化用例可能使 pytest 显示更多通过项，但必须全部 PASS）。

- [ ] **Step 6: 提交配置加载器**

```bash
git add config.example.json backend/app/services/agent_os_config.py backend/tests/test_agent_os_config.py
git add -p .gitignore
git commit -m "feat: add Agent OS runtime settings"
```

在 `git add -p` 中只暂存本任务新增的 `/config.local.json` 规则。当前工作区原有的
`.cursor/skills/**/config.local.json` 改动属于用户已有修改，不得随本提交暂存；如果
Git 将两处显示为同一 hunk，使用交互命令 `e` 仅保留本任务两行。

---

### Task 2: 通用 Agent OS invoke 客户端

**Files:**
- Create: `backend/app/services/agent_os.py`
- Create: `backend/tests/test_agent_os.py`

- [ ] **Step 1: 编写请求契约与直接响应测试**

创建 `backend/tests/test_agent_os.py`：

```python
import json

import httpx
import pytest

from app.services.agent_os import (
    AgentOSClient,
    AgentOSConfigurationError,
    AgentOSRequestError,
    AgentOSResponseError,
)
from app.services.agent_os_config import AgentOSSettings


def _settings(**overrides):
    values = {
        "base_url": "http://agent-os.test",
        "timeout_seconds": 180,
        "max_attempts": 3,
        "cookie": "",
        "header_name": "",
        "header_value": "",
    }
    values.update(overrides)
    return AgentOSSettings(**values)


@pytest.mark.asyncio
async def test_invoke_sends_explicit_app_name_input_and_auth():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"report_markdown": "# 报告"})

    client = AgentOSClient(
        _settings(
            cookie="session=secret",
            header_name="X-Agent-Key",
            header_value="header-secret",
        ),
        transport=httpx.MockTransport(handler),
    )

    result = await client.invoke_app(
        "tender_doc_interpreter_app", {"tender_text": "真实正文"}
    )

    assert result == {"report_markdown": "# 报告"}
    assert len(requests) == 1
    request = requests[0]
    assert request.url == httpx.URL("http://agent-os.test/v1/apps/invoke")
    assert json.loads(request.content) == {
        "appName": "tender_doc_interpreter_app",
        "input": {"tender_text": "真实正文"},
    }
    assert request.headers["cookie"] == "session=secret"
    assert request.headers["x-agent-key"] == "header-secret"


@pytest.mark.asyncio
async def test_invoke_returns_direct_object_without_runtime_envelope():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"value": 1})
    )
    result = await AgentOSClient(_settings(), transport=transport).invoke_app(
        "another_app", {"x": 1}
    )
    assert result == {"value": 1}


@pytest.mark.asyncio
async def test_missing_base_url_fails_without_sending_input():
    client = AgentOSClient(_settings(base_url=""))
    with pytest.raises(AgentOSConfigurationError, match="AGENT_OS_BASE_URL"):
        await client.invoke_app("app", {"secret_document": "must not leak"})
```

- [ ] **Step 2: 编写重试与响应校验测试**

在同一测试文件追加：

```python
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_retries_transient_statuses(status):
    calls = 0
    sleeps = []

    def handler(_request):
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(status, json={"error": "temporary"})
        return httpx.Response(200, json={"ok": True})

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    result = await AgentOSClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleep=fake_sleep,
    ).invoke_app("app", {})

    assert result == {"ok": True}
    assert calls == 3
    assert sleeps == [0.25, 0.5]


@pytest.mark.asyncio
async def test_does_not_retry_non_transient_4xx():
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": "bad input"})

    with pytest.raises(AgentOSRequestError, match="HTTP 400"):
        await AgentOSClient(
            _settings(), transport=httpx.MockTransport(handler)
        ).invoke_app("app", {"tender_text": "private"})
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_rejects_invalid_success_response(response):
    transport = httpx.MockTransport(lambda _request: response)
    with pytest.raises(AgentOSResponseError):
        await AgentOSClient(_settings(), transport=transport).invoke_app("app", {})


@pytest.mark.asyncio
async def test_final_error_does_not_contain_input_or_auth():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(503, json={"error": "down"})
    )
    with pytest.raises(AgentOSRequestError) as caught:
        await AgentOSClient(
            _settings(
                max_attempts=1,
                cookie="cookie-secret",
                header_name="X-Key",
                header_value="header-secret",
            ),
            transport=transport,
        ).invoke_app("safe_app", {"tender_text": "document-secret"})

    text = str(caught.value)
    assert "safe_app" in text
    assert "document-secret" not in text
    assert "cookie-secret" not in text
    assert "header-secret" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
async def test_retries_network_or_timeout_then_succeeds(error_type):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error_type("unavailable", request=request)
        return httpx.Response(200, json={"ok": True})

    async def no_sleep(_seconds):
        return None

    result = await AgentOSClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    ).invoke_app("app", {})
    assert result == {"ok": True}
    assert calls == 2
```

- [ ] **Step 3: 运行测试并确认因模块缺失而失败**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os.py -q
```

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'app.services.agent_os'`。

- [ ] **Step 4: 实现客户端、错误类型和重试**

创建 `backend/app/services/agent_os.py`：

```python
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.services.agent_os_config import (
    AgentOSConfigurationError,
    AgentOSSettings,
)


class AgentOSRequestError(RuntimeError):
    pass


class AgentOSResponseError(RuntimeError):
    pass


_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


class AgentOSClient:
    def __init__(
        self,
        settings: AgentOSSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep

    async def invoke_app(
        self, app_name: str, input_data: dict[str, object]
    ) -> dict[str, Any]:
        if not self._settings.base_url:
            raise AgentOSConfigurationError(
                "AGENT_OS_BASE_URL is required to invoke an Agent OS app"
            )
        headers = {}
        if self._settings.cookie:
            headers["Cookie"] = self._settings.cookie
        if self._settings.header_name and self._settings.header_value:
            headers[self._settings.header_name] = self._settings.header_value

        timeout = httpx.Timeout(self._settings.timeout_seconds)
        async with httpx.AsyncClient(
            base_url=self._settings.base_url,
            timeout=timeout,
            headers=headers,
            transport=self._transport,
        ) as client:
            for attempt in range(1, self._settings.max_attempts + 1):
                try:
                    response = await client.post(
                        "/v1/apps/invoke",
                        json={"appName": app_name, "input": input_data},
                    )
                except httpx.RequestError as exc:
                    if attempt == self._settings.max_attempts:
                        raise AgentOSRequestError(
                            f"{app_name}: request failed after {attempt} attempts"
                        ) from exc
                    await self._sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
                    continue

                if response.status_code in _RETRYABLE_STATUSES:
                    if attempt == self._settings.max_attempts:
                        raise AgentOSRequestError(
                            f"{app_name}: HTTP {response.status_code} "
                            f"after {attempt} attempts"
                        )
                    await self._sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
                    continue
                if response.is_error:
                    raise AgentOSRequestError(
                        f"{app_name}: HTTP {response.status_code}"
                    )

                try:
                    payload = response.json()
                except ValueError as exc:
                    raise AgentOSResponseError(
                        f"{app_name}: response is not valid JSON"
                    ) from exc
                if not isinstance(payload, dict):
                    raise AgentOSResponseError(
                        f"{app_name}: response must be a JSON object"
                    )
                return payload

        raise AssertionError("unreachable")
```

- [ ] **Step 5: 运行客户端测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_agent_os.py -q
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交通用客户端**

```bash
git add backend/app/services/agent_os.py backend/tests/test_agent_os.py
git commit -m "feat: add reusable Agent OS invoke client"
```

---

### Task 3: 招标文件真实内容提供器

**Files:**
- Create: `backend/app/services/tender_content.py`
- Create: `backend/tests/test_tender_content.py`

- [ ] **Step 1: 编写成功、partial 和停止测试**

创建 `backend/tests/test_tender_content.py`。测试使用隔离 SQLite，并 monkeypatch
`app.db.SessionLocal`；复用项目现有 `Base.metadata.create_all` 模式：

```python
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db as database
from app.models import Base, WorkspaceFile
from app.services.tender_content import (
    TenderContentError,
    TenderContentProvider,
    TenderContentStopped,
)


@pytest_asyncio.fixture
async def session_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'content.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(database, "SessionLocal", factory)
    yield factory
    await engine.dispose()


async def _seed_file(session_factory, tmp_path, *, status="succeeded", text="# 全文"):
    md_path = tmp_path / "tender.md"
    md_path.write_text(text, encoding="utf-8")
    async with session_factory() as session:
        session.add(
            WorkspaceFile(
                id="tender-file",
                task_id="task-1",
                label="招标文件",
                original_filename="tender.pdf",
                stored_path=str(tmp_path / "tender.pdf"),
                kind="document",
                ext=".pdf",
                parse_status=status,
                md_path=str(md_path),
            )
        )
        await session.commit()
    return md_path


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "partial"])
async def test_returns_real_markdown_for_usable_status(
    session_factory, tmp_path, status
):
    await _seed_file(session_factory, tmp_path, status=status, text="# 真实招标文件")
    provider = TenderContentProvider(timeout_seconds=1, poll_seconds=0.01)

    result = await provider.wait_for_markdown(
        "tender-file", stop_requested=lambda: False
    )

    assert result == "# 真实招标文件"


@pytest.mark.asyncio
async def test_waits_for_pending_file_to_finish(session_factory, tmp_path):
    md_path = await _seed_file(
        session_factory, tmp_path, status="pending", text="# 解析完成"
    )
    provider = TenderContentProvider(timeout_seconds=1, poll_seconds=0.01)

    async def finish_parse():
        await asyncio.sleep(0.03)
        async with session_factory() as session:
            wf = await session.get(WorkspaceFile, "tender-file")
            wf.parse_status = "succeeded"
            wf.md_path = str(md_path)
            await session.commit()

    finisher = asyncio.create_task(finish_parse())
    result = await provider.wait_for_markdown(
        "tender-file", stop_requested=lambda: False
    )
    await finisher
    assert result == "# 解析完成"


@pytest.mark.asyncio
async def test_stop_interrupts_parse_wait(session_factory, tmp_path):
    await _seed_file(session_factory, tmp_path, status="running")
    provider = TenderContentProvider(timeout_seconds=1, poll_seconds=0.01)
    stopped = False

    async def request_stop():
        nonlocal stopped
        await asyncio.sleep(0.03)
        stopped = True

    stopper = asyncio.create_task(request_stop())
    with pytest.raises(TenderContentStopped):
        await provider.wait_for_markdown(
            "tender-file", stop_requested=lambda: stopped
        )
    await stopper
```

- [ ] **Step 2: 编写失败、超时和空内容测试**

在同一测试文件追加：

```python
@pytest.mark.asyncio
async def test_failed_parse_raises_domain_error(session_factory, tmp_path):
    await _seed_file(session_factory, tmp_path, status="failed")
    provider = TenderContentProvider(timeout_seconds=1, poll_seconds=0.01)
    with pytest.raises(TenderContentError, match="parse failed"):
        await provider.wait_for_markdown(
            "tender-file", stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_pending_parse_times_out(session_factory, tmp_path):
    await _seed_file(session_factory, tmp_path, status="pending")
    provider = TenderContentProvider(timeout_seconds=0.03, poll_seconds=0.01)
    with pytest.raises(TenderContentError, match="timed out"):
        await provider.wait_for_markdown(
            "tender-file", stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_empty_markdown_is_rejected(session_factory, tmp_path):
    await _seed_file(session_factory, tmp_path, text=" \n")
    provider = TenderContentProvider(timeout_seconds=1, poll_seconds=0.01)
    with pytest.raises(TenderContentError, match="empty"):
        await provider.wait_for_markdown(
            "tender-file", stop_requested=lambda: False
        )


@pytest.mark.asyncio
async def test_missing_markdown_file_is_rejected(session_factory, tmp_path):
    md_path = await _seed_file(session_factory, tmp_path)
    md_path.unlink()
    provider = TenderContentProvider(timeout_seconds=1, poll_seconds=0.01)
    with pytest.raises(TenderContentError, match="file missing"):
        await provider.wait_for_markdown(
            "tender-file", stop_requested=lambda: False
        )
```

- [ ] **Step 3: 运行测试并确认因模块缺失而失败**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_tender_content.py -q
```

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'app.services.tender_content'`。

- [ ] **Step 4: 实现内容提供器**

创建 `backend/app/services/tender_content.py`：

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from app import db as database
from app.models import WorkspaceFile


class TenderContentError(RuntimeError):
    pass


class TenderContentStopped(Exception):
    pass


class TenderContentProvider:
    def __init__(self, *, timeout_seconds: float, poll_seconds: float = 0.1):
        self._timeout_seconds = timeout_seconds
        self._poll_seconds = poll_seconds

    async def wait_for_markdown(
        self,
        file_id: str,
        *,
        stop_requested: Callable[[], bool],
    ) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds

        while True:
            if stop_requested():
                raise TenderContentStopped()
            async with database.SessionLocal() as session:
                wf = await session.get(WorkspaceFile, file_id)
                if wf is None:
                    raise TenderContentError(
                        f"tender workspace file not found: {file_id}"
                    )
                status = wf.parse_status
                md_path = wf.md_path

            if status == "failed":
                raise TenderContentError(f"tender parse failed: {file_id}")
            if status in {"succeeded", "partial"}:
                if not md_path:
                    raise TenderContentError(
                        f"tender markdown path missing: {file_id}"
                    )
                path = Path(md_path)
                if not path.is_file():
                    raise TenderContentError(
                        f"tender markdown file missing: {file_id}"
                    )
                try:
                    markdown = await asyncio.to_thread(
                        path.read_text, encoding="utf-8"
                    )
                except OSError as exc:
                    raise TenderContentError(
                        f"tender markdown is unreadable: {file_id}"
                    ) from exc
                if not markdown.strip():
                    raise TenderContentError(
                        f"tender markdown is empty: {file_id}"
                    )
                return markdown

            if loop.time() >= deadline:
                raise TenderContentError(
                    f"tender parse timed out: {file_id} (status={status})"
                )
            await asyncio.sleep(self._poll_seconds)
```

- [ ] **Step 5: 运行内容提供器测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_tender_content.py -q
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交内容提供器**

```bash
git add backend/app/services/tender_content.py backend/tests/test_tender_content.py
git commit -m "feat: provide parsed tender document content"
```

---

### Task 4: Agent OS 招标文件解读适配器

**Files:**
- Modify: `backend/app/engine/base.py:32-39`
- Create: `backend/app/engine/interpretation_agent_os.py`
- Create: `backend/tests/test_interpretation_agent_os.py`

- [ ] **Step 1: 编写字段映射与输出校验测试**

创建 `backend/tests/test_interpretation_agent_os.py`：

```python
import pytest

from app.engine.interpretation_agent_os import (
    AgentOSInterpretationAgent,
    InterpretationResponseError,
)


class FakeAgentOSClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def invoke_app(self, app_name, input_data):
        self.calls.append((app_name, input_data))
        return self.response


@pytest.mark.asyncio
async def test_maps_tender_business_fields_and_report():
    client = FakeAgentOSClient({"report_markdown": "# 完整解读报告\n"})
    agent = AgentOSInterpretationAgent(client)

    result = await agent.interpret(
        task_id="T-1",
        tender_text="# 招标文件真实全文",
        background="市政项目",
        requirements="重点关注废标条款",
    )

    assert client.calls == [
        (
            "tender_doc_interpreter_app",
            {
                "tender_text": "# 招标文件真实全文",
                "project_background": "市政项目",
                "interpretation_requirements": "重点关注废标条款",
            },
        )
    ]
    assert result.title == "招标文件解读报告"
    assert result.markdown == "# 完整解读报告\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {},
        {"report_markdown": None},
        {"report_markdown": "  "},
    ],
)
async def test_rejects_missing_or_empty_report(response):
    agent = AgentOSInterpretationAgent(FakeAgentOSClient(response))
    with pytest.raises(InterpretationResponseError, match="report_markdown"):
        await agent.interpret(
            task_id="T-1",
            tender_text="全文",
            background="",
            requirements="",
        )
```

- [ ] **Step 2: 运行测试并确认因模块缺失而失败**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent_os.py -q
```

Expected: FAIL，包含
`ModuleNotFoundError: No module named 'app.engine.interpretation_agent_os'`。

- [ ] **Step 3: 修改解读协议**

将 `backend/app/engine/base.py` 中 `InterpretationAgent` 替换为：

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

- [ ] **Step 4: 实现业务适配器**

创建 `backend/app/engine/interpretation_agent_os.py`：

```python
from __future__ import annotations

from app.engine.base import InterpretationResult
from app.services.agent_os import AgentOSClient


TENDER_INTERPRETER_APP_NAME = "tender_doc_interpreter_app"


class InterpretationResponseError(RuntimeError):
    pass


class AgentOSInterpretationAgent:
    def __init__(
        self,
        client: AgentOSClient,
        *,
        app_name: str = TENDER_INTERPRETER_APP_NAME,
    ) -> None:
        self._client = client
        self._app_name = app_name

    async def interpret(
        self,
        *,
        task_id: str,
        tender_text: str,
        background: str,
        requirements: str,
    ) -> InterpretationResult:
        payload = await self._client.invoke_app(
            self._app_name,
            {
                "tender_text": tender_text,
                "project_background": background,
                "interpretation_requirements": requirements,
            },
        )
        markdown = payload.get("report_markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            raise InterpretationResponseError(
                f"{self._app_name}: report_markdown must be a non-empty string"
            )
        return InterpretationResult(markdown=markdown)
```

- [ ] **Step 5: 运行适配器和协议测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_interpretation_agent_os.py -q
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交业务适配器**

```bash
git add backend/app/engine/base.py backend/app/engine/interpretation_agent_os.py backend/tests/test_interpretation_agent_os.py
git commit -m "feat: adapt tender interpretation to Agent OS"
```

---

### Task 5: 调度器接入“解析 → Agent OS 解读”

**Files:**
- Modify: `backend/app/services/scheduler.py:1-13,224-278`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_scheduler.py:200-289`

- [ ] **Step 1: 在默认测试环境注入可控制的离线替身**

在 `backend/tests/conftest.py` 增加导入：

```python
from dataclasses import dataclass, field

import pytest

from app.engine.base import InterpretationResult
```

在 `client` fixture 前增加：

```python
class FakeTenderContentProvider:
    def __init__(self):
        self.markdown = "# 招标文件真实解析内容"
        self.calls = []

    async def wait_for_markdown(self, file_id, *, stop_requested):
        self.calls.append(file_id)
        return self.markdown


@dataclass
class FakeInterpretationAgent:
    calls: list[dict] = field(default_factory=list)
    error: Exception | None = None
    gate: object | None = None

    async def interpret(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        if self.error is not None:
            raise self.error
        return InterpretationResult(markdown="# 离线智能体解读\n")


@pytest.fixture
def interpretation_dependencies(monkeypatch):
    provider = FakeTenderContentProvider()
    agent = FakeInterpretationAgent()
    monkeypatch.setattr(
        scheduler,
        "_build_interpretation_services",
        lambda: (provider, agent),
    )
    return provider, agent
```

将 `client` fixture 签名改为依赖该 fixture，确保全部默认 API 测试不访问 Agent OS：

```python
@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch, interpretation_dependencies):
```

- [ ] **Step 2: 编写调度器字段传递和失败测试**

将 `test_scheduler_runs_to_completion` 增加
`interpretation_dependencies` 参数，并在原断言后追加：

```python
    provider, agent = interpretation_dependencies
    assert len(provider.calls) == 1
    assert provider.calls[0]
    assert agent.calls == [
        {
            "task_id": task_id,
            "tender_text": "# 招标文件真实解析内容",
            "background": "bg",
            "requirements": "req",
        }
    ]
```

将 `test_interpret_failure_marks_failed_without_diagnosis` 重写为：

```python
@pytest.mark.asyncio
async def test_interpret_failure_marks_failed_without_diagnosis(
    client, interpretation_dependencies
):
    _provider, agent = interpretation_dependencies
    agent.error = RuntimeError("interpret boom")
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=5)

    assert status == "failed"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["results"] == []
    assert "interpret boom" in (detail.get("error_message") or "")
    assert detail.get("interpret_markdown", "") == ""
    assert (await client.get(f"/api/tasks/{task_id}/report.docx")).status_code == 404
```

- [ ] **Step 3: 改写 interpreting 阶段暂停与停止测试**

删除对 `MockInterpretationAgent.interpret` 的 monkeypatch。两个测试都通过
`interpretation_dependencies` 控制 Agent：

```python
@pytest.mark.asyncio
async def test_cannot_pause_while_interpreting(
    client, interpretation_dependencies
):
    gate = asyncio.Event()
    _provider, agent = interpretation_dependencies
    agent.gate = gate
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "interpreting":
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("never saw interpreting status")

    response = await client.post(f"/api/tasks/{task_id}/pause")
    assert response.status_code == 409
    gate.set()
    await scheduler.wait_for_terminal(task_id, timeout=5)
```

停止测试保留“迟到结果不得落盘”的断言：

```python
@pytest.mark.asyncio
async def test_stop_during_interpreting_discards_late_response(
    client, interpretation_dependencies
):
    gate = asyncio.Event()
    _provider, agent = interpretation_dependencies
    agent.gate = gate
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]

    for _ in range(100):
        data = (await client.get(f"/api/tasks/{task_id}")).json()
        if data["status"] == "interpreting":
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("never saw interpreting status")

    stop_request = asyncio.create_task(
        client.post(f"/api/tasks/{task_id}/stop")
    )
    await asyncio.sleep(0.05)
    gate.set()
    response = await stop_request
    assert response.status_code == 200

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"
    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["interpret_md_path"] is None
    assert detail["interpret_html_path"] is None
    assert detail["results"] == []
```

- [ ] **Step 4: 运行 scheduler 测试并确认生产接线尚未完成**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_scheduler.py -q
```

Expected: FAIL，原因包括 `_build_interpretation_services` 尚不存在，或解读调用仍使用
`tender_path` 而不是 `tender_text`。

- [ ] **Step 5: 实现依赖构造函数**

修改 `backend/app/services/scheduler.py` 导入，移除
`MOCK_INTERPRET_DELAY_SECONDS` 和 `MockInterpretationAgent`：

```python
from app.config import MOCK_ITEM_DELAY_SECONDS
from app.engine.interpretation_agent_os import AgentOSInterpretationAgent
from app.services import interpret_report, report
from app.services.agent_os import AgentOSClient
from app.services.agent_os_config import (
    load_agent_os_settings,
    load_tender_content_settings,
)
from app.services.tender_content import (
    TenderContentProvider,
    TenderContentStopped,
)
```

在 `_run` 前增加：

```python
def _build_interpretation_services():
    agent_settings = load_agent_os_settings()
    tender_settings = load_tender_content_settings()
    provider = TenderContentProvider(
        timeout_seconds=tender_settings.parse_wait_timeout_seconds
    )
    agent = AgentOSInterpretationAgent(AgentOSClient(agent_settings))
    return provider, agent
```

- [ ] **Step 6: 将 scheduler 解读段切换为真实解析内容**

在 `_run` 初始数据库读取段中，替换/补充变量：

```python
            tender_file_id = task.tender_file_id
            tender_path = task.tender_path
            bid_path = task.bid_path
            background = task.background or ""
            requirements = task.requirements or ""
```

将硬编码 Mock 解读调用替换为：

```python
        if need_interpret:
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return
            if not tender_file_id:
                raise RuntimeError("tender workspace file id is missing")

            content_provider, agent = _build_interpretation_services()
            try:
                tender_text = await content_provider.wait_for_markdown(
                    tender_file_id,
                    stop_requested=lambda: _should_stop(task_id),
                )
            except TenderContentStopped:
                await _mark_stopped(task_id)
                return

            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            interpret_result = await agent.interpret(
                task_id=task_id,
                tender_text=tender_text,
                background=background,
                requirements=requirements,
            )
            if _should_stop(task_id):
                await _mark_stopped(task_id)
                return

            md_path, html_path = interpret_report.save_interpret_reports(
                task_id, interpret_result
            )
```

保留该段之后现有的数据库更新逻辑。诊断阶段仍使用
`{"tender_path": tender_path, "bid_path": bid_path}`，本次不改诊断引擎输入。

- [ ] **Step 7: 运行 scheduler 测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_scheduler.py -q
```

Expected: 全部 PASS。

- [ ] **Step 8: 提交 scheduler 接线**

```bash
git add backend/app/services/scheduler.py backend/tests/conftest.py backend/tests/test_scheduler.py
git commit -m "feat: invoke tender agent after document parsing"
```

---

### Task 6: 移除 Mock 遗留并完成回归验证

**Files:**
- Modify: `backend/app/config.py:12-15`
- Delete: `backend/app/engine/interpretation_mock.py`
- Delete: `backend/tests/test_interpretation_agent.py`
- Test: `backend/tests/`

- [ ] **Step 1: 删除失效配置与 Mock 文件**

将 `backend/app/config.py` 末尾整理为：

```python
MOCK_ITEM_DELAY_SECONDS = 0.8
```

删除：

```text
backend/app/engine/interpretation_mock.py
backend/tests/test_interpretation_agent.py
```

同时从 `backend/tests/conftest.py` 删除以下旧 monkeypatch：

```python
monkeypatch.setattr("app.config.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
monkeypatch.setattr("app.services.scheduler.MOCK_INTERPRET_DELAY_SECONDS", 0.01)
```

- [ ] **Step 2: 搜索并清理生产代码中的旧引用**

Run:

```bash
rg "MockInterpretationAgent|MOCK_INTERPRET_DELAY_SECONDS|INTERPRETATION_AGENT_URL|INTERPRETATION_AGENT =" backend
```

Expected: 无输出。历史设计文档中的记录不需要修改。

- [ ] **Step 3: 运行新组件测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest \
  tests/test_agent_os_config.py \
  tests/test_agent_os.py \
  tests/test_tender_content.py \
  tests/test_interpretation_agent_os.py \
  tests/test_scheduler.py -q
```

Expected: 全部 PASS。

- [ ] **Step 4: 运行完整后端测试**

Run:

```bash
cd backend && ../.venv/bin/python -m pytest -q
```

Expected: 全部 PASS，无网络访问、无后台任务泄漏警告。

- [ ] **Step 5: 检查格式和工作区差异**

Run:

```bash
git diff --check
git status --short
```

Expected:

- `git diff --check` 无输出并返回 0。
- 只出现本任务文件以及用户原有的未提交文件；不得覆盖用户已有修改。

- [ ] **Step 6: 提交清理与回归结果**

```bash
git add backend/app/config.py backend/app/engine/interpretation_mock.py backend/tests/test_interpretation_agent.py backend/tests/conftest.py
git commit -m "refactor: remove mock tender interpretation"
```

---

## 手动联调清单

自动化测试通过后，仅在本地 Agent OS 已可访问且应用仍为 `published` 时执行：

1. 复制 `config.example.json` 为 `config.local.json`，填写 `agentOs.baseUrl` 和必要鉴权。
2. 启动项目，上传可正常解析的真实招标文件和投标文件。
3. 确认任务在招标文件解析期间保持 `interpreting`。
4. 确认请求使用 `/v1/apps/invoke` 和 `tender_doc_interpreter_app`。
5. 确认解读报告来自响应 `report_markdown`，Markdown/HTML 均可查看。
6. 确认解读完成后才进入诊断。
7. 在解析等待和 Agent OS 调用期间分别测试停止任务，确认不会保存迟到报告。

不得把 `config.local.json`、Cookie、Header 值或招标文件全文提交到 Git 或输出到日志。
