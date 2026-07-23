from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

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
    parse_wait_timeout_seconds: float = 1800.0
    interpret_invoke_timeout_seconds: float = 1200.0
    checklist_invoke_timeout_seconds: float = 600.0
    interpret_html_invoke_timeout_seconds: float = 600.0
    batch_diagnosis_index_wait_timeout_seconds: float = 7200.0


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
    tender_interp = (
        local.get("tenderInterpretation")
        if isinstance(local.get("tenderInterpretation"), dict)
        else {}
    )
    batch_diagnosis = (
        local.get("batchDiagnosis")
        if isinstance(local.get("batchDiagnosis"), dict)
        else {}
    )
    tender_checklist = (
        local.get("tenderChecklist")
        if isinstance(local.get("tenderChecklist"), dict)
        else {}
    )
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
        parse_wait_timeout_seconds=_as_float(
            _env_or(
                tender_interp.get("parseWaitTimeoutSeconds"),
                "TENDER_PARSE_WAIT_TIMEOUT_SECONDS",
                1800,
            ),
            1800.0,
        ),
        interpret_invoke_timeout_seconds=_as_float(
            _env_or(
                tender_interp.get("invokeTimeoutSeconds"),
                "TENDER_INTERPRET_INVOKE_TIMEOUT_SECONDS",
                1200,
            ),
            1200.0,
        ),
        checklist_invoke_timeout_seconds=_as_float(
            _env_or(
                tender_checklist.get("invokeTimeoutSeconds"),
                "TENDER_CHECKLIST_INVOKE_TIMEOUT_SECONDS",
                600,
            ),
            600.0,
        ),
        interpret_html_invoke_timeout_seconds=_as_float(
            _env_or(
                local.get("interpretHtmlInvokeTimeoutSeconds"),
                "INTERPRET_HTML_INVOKE_TIMEOUT_SECONDS",
                600,
            ),
            600.0,
        ),
        batch_diagnosis_index_wait_timeout_seconds=_as_float(
            _env_or(
                batch_diagnosis.get("indexWaitTimeoutSeconds"),
                "BATCH_DIAGNOSIS_INDEX_WAIT_TIMEOUT_SECONDS",
                7200,
            ),
            7200.0,
        ),
    )


def _backoff_seconds(attempt: int) -> float:
    return min(2.0**attempt, 8.0)


def _format_log_context(log_context: Mapping[str, object] | None) -> str:
    if not log_context:
        return ""
    parts = [f"{key}={value}" for key, value in log_context.items()]
    return " " + " ".join(parts)


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
        *,
        log_context: Mapping[str, object] | None = None,
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
        context_suffix = _format_log_context(log_context)
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self._transport,
            trust_env=False,
        ) as client:
            for attempt in range(attempts):
                attempt_no = attempt + 1
                started_at = time.monotonic()
                logger.info(
                    "Agent OS invoke start app=%s attempt=%d/%d timeout_s=%.0f%s",
                    app_name,
                    attempt_no,
                    attempts,
                    settings.timeout_seconds,
                    context_suffix,
                )
                try:
                    response = await client.post(url, json=body, headers=headers)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    elapsed_s = time.monotonic() - started_at
                    last_error = AgentOSError(
                        f"Agent OS invoke transport error for app {app_name}: {type(exc).__name__}",
                        app_name=app_name,
                        retryable=True,
                    )
                    if attempt_no >= attempts:
                        logger.error(
                            "Agent OS invoke failed app=%s attempt=%d/%d "
                            "reason=transport_error error=%s elapsed_s=%.2f%s",
                            app_name,
                            attempt_no,
                            attempts,
                            type(exc).__name__,
                            elapsed_s,
                            context_suffix,
                        )
                        raise last_error from exc
                    backoff_s = _backoff_seconds(attempt)
                    logger.warning(
                        "Agent OS invoke retry app=%s attempt=%d/%d "
                        "reason=transport_error error=%s elapsed_s=%.2f backoff_s=%.0f%s",
                        app_name,
                        attempt_no,
                        attempts,
                        type(exc).__name__,
                        elapsed_s,
                        backoff_s,
                        context_suffix,
                    )
                    await asyncio.sleep(backoff_s)
                    continue
                elapsed_s = time.monotonic() - started_at
                if response.status_code in _RETRYABLE_STATUS:
                    last_error = AgentOSError(
                        f"Agent OS invoke retryable HTTP {response.status_code} for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                        retryable=True,
                    )
                    if attempt_no >= attempts:
                        logger.error(
                            "Agent OS invoke failed app=%s attempt=%d/%d "
                            "reason=http_%d elapsed_s=%.2f%s",
                            app_name,
                            attempt_no,
                            attempts,
                            response.status_code,
                            elapsed_s,
                            context_suffix,
                        )
                        raise last_error
                    backoff_s = _backoff_seconds(attempt)
                    logger.warning(
                        "Agent OS invoke retry app=%s attempt=%d/%d "
                        "reason=http_%d elapsed_s=%.2f backoff_s=%.0f%s",
                        app_name,
                        attempt_no,
                        attempts,
                        response.status_code,
                        elapsed_s,
                        backoff_s,
                        context_suffix,
                    )
                    await asyncio.sleep(backoff_s)
                    continue
                if response.status_code >= 400:
                    logger.error(
                        "Agent OS invoke failed app=%s attempt=%d/%d "
                        "reason=http_%d elapsed_s=%.2f%s",
                        app_name,
                        attempt_no,
                        attempts,
                        response.status_code,
                        elapsed_s,
                        context_suffix,
                    )
                    raise AgentOSError(
                        f"Agent OS invoke HTTP {response.status_code} for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                        retryable=False,
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    logger.error(
                        "Agent OS invoke failed app=%s attempt=%d/%d "
                        "reason=non_json_response elapsed_s=%.2f%s",
                        app_name,
                        attempt_no,
                        attempts,
                        elapsed_s,
                        context_suffix,
                    )
                    raise AgentOSResponseError(
                        f"Agent OS invoke returned non-JSON for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                    ) from exc
                if not isinstance(payload, dict):
                    logger.error(
                        "Agent OS invoke failed app=%s attempt=%d/%d "
                        "reason=non_object_json elapsed_s=%.2f%s",
                        app_name,
                        attempt_no,
                        attempts,
                        elapsed_s,
                        context_suffix,
                    )
                    raise AgentOSResponseError(
                        f"Agent OS invoke returned non-object JSON for app {app_name}",
                        app_name=app_name,
                        status_code=response.status_code,
                    )
                logger.info(
                    "Agent OS invoke success app=%s attempt=%d/%d elapsed_s=%.2f%s",
                    app_name,
                    attempt_no,
                    attempts,
                    elapsed_s,
                    context_suffix,
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
