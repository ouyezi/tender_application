from __future__ import annotations

from typing import Any, get_type_hints

import pytest

import app.engine.interpretation_agent_os as interpretation_agent_os
from app.engine.interpretation_agent_os import (
    TENDER_INTERPRETER_APP_NAME,
    AgentOSInterpretationAgent,
    InterpretationResponseError,
)


class FakeAgentOSClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def invoke_app(
        self, app_name: str, input_data: dict[str, object]
    ) -> dict[str, Any]:
        self.calls.append((app_name, input_data))
        return self.response


def test_constructor_depends_on_minimal_invoker_protocol() -> None:
    hints = get_type_hints(AgentOSInterpretationAgent.__init__)

    assert hints["client"] is interpretation_agent_os.AgentOSInvoker


@pytest.mark.asyncio
async def test_invokes_published_app_with_exact_input_schema() -> None:
    client = FakeAgentOSClient({"output": "# 解读报告\n"})
    agent = AgentOSInterpretationAgent(client)

    result = await agent.interpret(
        task_id="TASK-SHOULD-NOT-BE-SENT",
        tender_text="招标正文",
        background="项目背景",
        requirements="重点关注废标条款",
    )

    assert TENDER_INTERPRETER_APP_NAME == "tender_doc_interpreter_app"
    assert client.calls == [
        (
            "tender_doc_interpreter_app",
            {
                "tender_text": "招标正文",
                "project_background": "项目背景",
                "interpretation_requirements": "重点关注废标条款",
            },
        )
    ]
    assert result.title == "招标文件解读报告"


@pytest.mark.asyncio
async def test_passes_empty_optional_text_fields_unchanged() -> None:
    client = FakeAgentOSClient({"output": "报告"})

    await AgentOSInterpretationAgent(client).interpret(
        task_id="TASK-1",
        tender_text="正文",
        background="",
        requirements="",
    )

    assert client.calls[0][1] == {
        "tender_text": "正文",
        "project_background": "",
        "interpretation_requirements": "",
    }


@pytest.mark.asyncio
async def test_allows_custom_app_name_injection() -> None:
    client = FakeAgentOSClient({"output": "报告"})

    await AgentOSInterpretationAgent(
        client, app_name="test-interpreter"
    ).interpret(
        task_id="TASK-1",
        tender_text="正文",
        background="背景",
        requirements="要求",
    )

    assert client.calls[0][0] == "test-interpreter"


@pytest.mark.asyncio
async def test_preserves_markdown_exactly() -> None:
    markdown = "\n  # 标题\n\n- 条目  \n"
    client = FakeAgentOSClient({"output": markdown})

    result = await AgentOSInterpretationAgent(client).interpret(
        task_id="TASK-1",
        tender_text="正文",
        background="背景",
        requirements="要求",
    )

    assert result.markdown == markdown


@pytest.mark.asyncio
async def test_accepts_legacy_report_markdown_field() -> None:
    markdown = "# 招标文件解读报告\n\n## 一、项目基础信息\n"
    client = FakeAgentOSClient({"report_markdown": markdown})

    result = await AgentOSInterpretationAgent(client).interpret(
        task_id="TASK-1",
        tender_text="正文",
        background="背景",
        requirements="要求",
    )

    assert result.markdown == markdown


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"output": None},
        {"output": 123},
        {"output": " \n\t "},
        {"report_markdown": " \n\t "},
    ],
    ids=["missing", "none", "non-string", "blank-output", "blank-legacy"],
)
@pytest.mark.asyncio
async def test_rejects_invalid_report_without_leaking_response(
    response: dict[str, Any],
) -> None:
    sensitive_tender = "机密招标正文-DO-NOT-LEAK"
    sensitive_response = "机密响应内容-DO-NOT-LEAK"
    app_name = "diagnostic-test-interpreter"
    response["other_output"] = sensitive_response
    client = FakeAgentOSClient(response)

    with pytest.raises(InterpretationResponseError) as caught:
        await AgentOSInterpretationAgent(client, app_name=app_name).interpret(
            task_id="TASK-1",
            tender_text=sensitive_tender,
            background="背景",
            requirements="要求",
        )

    message = str(caught.value)
    assert app_name in message
    assert "markdown output" in message
    assert sensitive_tender not in message
    assert sensitive_response not in message
    assert repr(response) not in message
