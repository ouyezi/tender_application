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
