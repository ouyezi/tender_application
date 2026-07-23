import json

import pytest

from app.engine.interpret_html_agent_os import (
    TENDER_INTERPRET_HTML_REPORT_APP_NAME,
    AgentOSInterpretHtmlAgent,
    InterpretHtmlAgentResponseError,
)
from app.interpret_html_schema import InterpretHtmlReportData
from tests.test_interpret_html_schema import MINIMAL_PAYLOAD


class FakeClient:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[tuple[str, dict[str, object], dict | None]] = []

    async def invoke_app(self, app_name, input_data, log_context=None):
        self.calls.append((app_name, input_data, log_context))
        return self.response


@pytest.mark.asyncio
async def test_invoke_passes_interpret_report():
    client = FakeClient({"output": json.dumps(MINIMAL_PAYLOAD)})
    agent = AgentOSInterpretHtmlAgent(client=client)
    result = await agent.generate(task_id="T-1", interpret_report="# 解读\n")
    assert isinstance(result, InterpretHtmlReportData)
    assert client.calls[0][0] == TENDER_INTERPRET_HTML_REPORT_APP_NAME
    assert client.calls[0][1]["interpret_report"] == "# 解读\n"


@pytest.mark.asyncio
async def test_raises_on_invalid_json():
    client = FakeClient({"output": "not json"})
    agent = AgentOSInterpretHtmlAgent(client=client)
    with pytest.raises(InterpretHtmlAgentResponseError):
        await agent.generate(task_id="T-1", interpret_report="# x")
