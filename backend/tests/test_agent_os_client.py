import json

import httpx
import pytest

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


def test_load_settings_parse_wait_timeout(tmp_path, monkeypatch):
    cfg = tmp_path / "config.local.json"
    cfg.write_text(
        json.dumps(
            {
                "agentOs": {"baseUrl": "http://localhost:8000"},
                "tenderInterpretation": {"parseWaitTimeoutSeconds": 600},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", cfg)
    monkeypatch.delenv("TENDER_PARSE_WAIT_TIMEOUT_SECONDS", raising=False)
    settings = agent_os.load_settings()
    assert settings.parse_wait_timeout_seconds == 600.0

    monkeypatch.setenv("TENDER_PARSE_WAIT_TIMEOUT_SECONDS", "100")
    settings = agent_os.load_settings()
    assert settings.parse_wait_timeout_seconds == 100.0


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


def test_load_settings_missing_base_url_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    for key in ("AGENT_OS_BASE_URL",):
        monkeypatch.delenv(key, raising=False)
    settings = agent_os.load_settings()
    assert settings.base_url == ""


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


@pytest.mark.asyncio
async def test_invoke_app_unwraps_structured_output(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_os, "LOCAL_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setenv("AGENT_OS_BASE_URL", "http://agent-os.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "requestId": "rec_1",
                "status": "completed",
                "output": "ignored",
                "structuredOutput": {"vector_query": "q", "keywords_json": "[]"},
            },
        )

    client = agent_os.AgentOSClient(transport=httpx.MockTransport(handler))
    result = await client.invoke_app("demo_app", {"query": "x"})
    assert result == {"vector_query": "q", "keywords_json": "[]"}
