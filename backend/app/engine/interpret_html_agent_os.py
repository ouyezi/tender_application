from __future__ import annotations

import json
from typing import Any, Optional, Protocol

from app.interpret_html_schema import InterpretHtmlReportData
from app.services.agent_os import AgentOSClient

TENDER_INTERPRET_HTML_REPORT_APP_NAME = "tender_interpret_html_report_app"


class AgentOSInvoker(Protocol):
    async def invoke_app(
        self,
        app_name: str,
        input_data: dict[str, object],
        *,
        log_context: dict[str, object] | None = None,
    ) -> dict[str, Any]: ...


class InterpretHtmlAgentResponseError(RuntimeError):
    pass


def _extract_json(response: dict[str, Any]) -> dict[str, Any]:
    for key in ("report_json", "output", "result"):
        raw = response.get(key)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise InterpretHtmlAgentResponseError(
                    f"invalid JSON in agent response field {key!r}"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
    raise InterpretHtmlAgentResponseError("missing JSON in agent response")


class AgentOSInterpretHtmlAgent:
    def __init__(
        self,
        client: Optional[AgentOSInvoker] = None,
        *,
        app_name: str = TENDER_INTERPRET_HTML_REPORT_APP_NAME,
    ) -> None:
        self._client = client
        self._app_name = app_name

    async def generate(
        self,
        *,
        task_id: str,
        interpret_report: str,
    ) -> InterpretHtmlReportData:
        client = self._client or AgentOSClient()
        payload = await client.invoke_app(
            self._app_name,
            {"interpret_report": interpret_report},
            log_context={"task_id": task_id},
        )
        data = _extract_json(payload)
        return InterpretHtmlReportData.model_validate(data)
