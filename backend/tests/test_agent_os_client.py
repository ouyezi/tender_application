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
