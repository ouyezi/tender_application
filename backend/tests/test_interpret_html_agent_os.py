import json

import pytest

from app.engine.interpret_html_agent_os import (
    TENDER_INTERPRET_HTML_REPORT_APP_NAME,
    AgentOSInterpretHtmlAgent,
    InterpretHtmlAgentResponseError,
    _extract_json,
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


AGENT_VARIANT_PAYLOAD = {
    "schema_version": "1",
    "meta": {
        "title": "铁建福利商城员工福利物资谈判采购项目解读报告",
        "subtitle": "采购编号: ZTGY-WZ-2026-FLCG01 | 截标时间: 2026年05月13日 09:00",
        "project_key": "tender_ZTGYWZ2026FLCG01",
    },
    "overview": {
        "rows": [{"label": "项目名称", "value": "铁建福利商城员工福利物资谈判采购项目"}]
    },
    "risks": [
        {
            "level": "high",
            "title": "业绩关联性陷阱",
            "description": "若使用集团内子公司业绩，必须提供清晰的股权关系证明。",
        }
    ],
    "tasks": [
        {"name": "准备央企业绩证明材料", "owner": "商务/销售", "deadline": "2026-05-10"}
    ],
    "checklist": [
        {
            "group_name": "废标红线检查",
            "redline": True,
            "items": ["营业执照及法人资格证明"],
        }
    ],
    "key_info": {
        "timeline": {"bid_submission_deadline": "2026-05-13 09:00"},
        "qualification": {"legal_entity": "境内注册独立法人"},
        "commercial": {"settlement_entity": "北京中铁工业有限公司"},
        "technical": {"integration_method": "API或H5"},
    },
    "strategy": {
        "advantage": "突出央企合作业绩",
        "risk_avoid": "严格核对签字盖章",
        "price": "报价需谨慎测算",
    },
    "scoring": [
        {
            "dimension": "商务评审",
            "weight_range": "30-40分",
            "criteria": "企业业绩、财务状况",
        }
    ],
}


@pytest.mark.asyncio
async def test_invoke_passes_interpret_report():
    client = FakeClient({"output": json.dumps(MINIMAL_PAYLOAD)})
    agent = AgentOSInterpretHtmlAgent(client=client)
    result = await agent.generate(task_id="T-1", interpret_report="# 解读\n")
    assert isinstance(result, InterpretHtmlReportData)
    assert client.calls[0][0] == TENDER_INTERPRET_HTML_REPORT_APP_NAME
    assert client.calls[0][1]["interpret_report"] == "# 解读\n"


@pytest.mark.asyncio
async def test_accepts_structured_output_payload():
    client = FakeClient(AGENT_VARIANT_PAYLOAD)
    agent = AgentOSInterpretHtmlAgent(client=client)
    result = await agent.generate(task_id="T-1", interpret_report="# 解读\n")
    assert result.meta.project_key == "tender_ZTGYWZ2026FLCG01"
    assert result.risks[0].desc.startswith("若使用集团内子公司业绩")
    assert result.tasks.p0[0].name == "准备央企业绩证明材料"
    assert result.checklist[0].section == "废标红线检查"
    assert result.key_info.qualification[0].value == "境内注册独立法人"
    assert result.scoring[0].weight == "30-40分"


def test_extract_json_normalizes_agent_variant_payload():
    data = _extract_json(AGENT_VARIANT_PAYLOAD)
    assert data["risks"][0]["desc"].startswith("若使用集团内子公司业绩")
    assert data["tasks"]["p0"][0]["name"] == "准备央企业绩证明材料"
    assert data["checklist"][0]["section"] == "废标红线检查"


@pytest.mark.asyncio
async def test_raises_on_invalid_json():
    client = FakeClient({"output": "not json"})
    agent = AgentOSInterpretHtmlAgent(client=client)
    with pytest.raises(InterpretHtmlAgentResponseError):
        await agent.generate(task_id="T-1", interpret_report="# x")
