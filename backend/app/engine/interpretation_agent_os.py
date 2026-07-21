from __future__ import annotations

from typing import Any, Protocol

from app.engine.base import InterpretationResult


TENDER_INTERPRETER_APP_NAME = "tender_doc_interpreter_app"


class AgentOSInvoker(Protocol):
    async def invoke_app(
        self, app_name: str, input_data: dict[str, object]
    ) -> dict[str, Any]: ...


class InterpretationResponseError(RuntimeError):
    pass


def _extract_report_markdown(response: dict[str, Any]) -> str:
    report_markdown = response.get("report_markdown")
    if isinstance(report_markdown, str) and report_markdown.strip():
        return report_markdown

    output = response.get("output")
    if isinstance(output, str) and output.strip():
        return output

    raise InterpretationResponseError(
        "Agent OS interpretation response for app "
        f"{TENDER_INTERPRETER_APP_NAME!r} has empty or invalid markdown output"
    )


class AgentOSInterpretationAgent:
    def __init__(
        self,
        client: AgentOSInvoker,
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
        input_data = {
            "tender_text": tender_text,
            "project_background": background,
            "interpretation_requirements": requirements,
        }
        response = await self._client.invoke_app(self._app_name, input_data)
        try:
            report_markdown = _extract_report_markdown(response)
        except InterpretationResponseError as exc:
            raise InterpretationResponseError(
                "Agent OS interpretation response for app "
                f"{self._app_name!r} has empty or invalid markdown output"
            ) from exc
        return InterpretationResult(markdown=report_markdown)
