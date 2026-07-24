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


def _is_report_payload(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("schema_version") == "1"
        and isinstance(value.get("meta"), dict)
    )


def _normalize_key_value_rows(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        rows: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            cell_value = item.get("value")
            if not label:
                continue
            rows.append({"label": label, "value": str(cell_value or "")})
        return rows
    if isinstance(value, dict):
        rows = []
        for label, cell_value in value.items():
            label_str = str(label).strip()
            if not label_str:
                continue
            if isinstance(cell_value, list):
                value_str = "、".join(str(part) for part in cell_value if str(part).strip())
            else:
                value_str = str(cell_value or "")
            rows.append({"label": label_str, "value": value_str})
        return rows
    return []


def _normalize_timeline_rows(value: Any) -> list[dict[str, str]]:
    if isinstance(value, list):
        rows: list[dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "label": str(item.get("label") or ""),
                    "value": str(item.get("value") or ""),
                    "note": str(item.get("note") or ""),
                }
            )
        return rows
    return [
        {"label": row["label"], "value": row["value"], "note": ""}
        for row in _normalize_key_value_rows(value)
    ]


def _normalize_tasks(value: Any) -> dict[str, list[dict[str, str]]]:
    if isinstance(value, dict):
        return {
            key: [
                {
                    "name": str(item.get("name") or ""),
                    "owner": str(item.get("owner") or ""),
                    "deadline": str(item.get("deadline") or ""),
                }
                for item in (value.get(key) or [])
                if isinstance(item, dict)
            ]
            for key in ("p0", "p1", "p2")
        }
    if isinstance(value, list):
        return {
            "p0": [
                {
                    "name": str(item.get("name") or ""),
                    "owner": str(item.get("owner") or ""),
                    "deadline": str(item.get("deadline") or ""),
                }
                for item in value
                if isinstance(item, dict)
            ],
            "p1": [],
            "p2": [],
        }
    return {"p0": [], "p1": [], "p2": []}


def _normalize_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["risks"] = [
        {
            "level": item.get("level"),
            "title": str(item.get("title") or ""),
            "desc": str(item.get("desc") or item.get("description") or ""),
        }
        for item in (payload.get("risks") or [])
        if isinstance(item, dict) and item.get("level") in {"high", "mid", "low"}
    ]
    normalized["tasks"] = _normalize_tasks(payload.get("tasks"))
    normalized["checklist"] = [
        {
            "section": str(item.get("section") or item.get("group_name") or ""),
            "items": [
                str(entry)
                for entry in (item.get("items") or [])
                if str(entry).strip()
            ],
            "redline": bool(item.get("redline")),
        }
        for item in (payload.get("checklist") or [])
        if isinstance(item, dict)
    ]
    key_info = payload.get("key_info")
    if isinstance(key_info, dict):
        normalized["key_info"] = {
            "timeline": _normalize_timeline_rows(key_info.get("timeline")),
            "qualification": _normalize_key_value_rows(key_info.get("qualification")),
            "commercial": _normalize_key_value_rows(key_info.get("commercial")),
            "technical": _normalize_key_value_rows(key_info.get("technical")),
        }
    normalized["scoring"] = [
        {
            "dimension": str(item.get("dimension") or ""),
            "score": str(item.get("score") or item.get("weight_range") or item.get("weight") or ""),
            "weight": str(item.get("weight") or item.get("weight_range") or ""),
            "criteria": str(item.get("criteria") or ""),
            "strategy": str(item.get("strategy") or ""),
        }
        for item in (payload.get("scoring") or [])
        if isinstance(item, dict)
    ]
    return normalized


def _extract_json(response: dict[str, Any]) -> dict[str, Any]:
    if _is_report_payload(response):
        return _normalize_report_payload(response)
    for key in ("report_json", "output", "result"):
        raw = response.get(key)
        if isinstance(raw, dict):
            if _is_report_payload(raw):
                return _normalize_report_payload(raw)
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise InterpretHtmlAgentResponseError(
                    f"invalid JSON in agent response field {key!r}"
                ) from exc
            if isinstance(parsed, dict):
                if _is_report_payload(parsed):
                    return _normalize_report_payload(parsed)
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
