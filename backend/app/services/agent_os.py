from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from app.config import ROOT

logger = logging.getLogger(__name__)
LOCAL_CONFIG_PATH = ROOT / "config.local.json"
_RETRYABLE_STATUS = frozenset({429, 502, 503, 504})


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
                # Production invoke wraps business fields in structuredOutput when
                # formatOutput is enabled; adapters expect the inner object.
                structured = payload.get("structuredOutput")
                if isinstance(structured, dict):
                    return structured
                return payload
        raise last_error or AgentOSError(
            f"Agent OS invoke failed for app {app_name}",
            app_name=app_name,
        )
