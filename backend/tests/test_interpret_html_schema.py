import pytest
from pydantic import ValidationError

from app.interpret_html_schema import InterpretHtmlReportData


MINIMAL_PAYLOAD = {
    "schema_version": "1",
    "meta": {
        "title": "项目 — 招标分析报告",
        "subtitle": "招标编号：X | 截标：2026-07-27",
        "project_key": "tender_test",
    },
    "overview": {
        "rows": [
            {"label": "项目名称", "value": "测试", "label2": "编号", "value2": "N-1"}
        ]
    },
    "risks": [{"level": "high", "title": "风险", "desc": "说明"}],
    "tasks": {"p0": [], "p1": [], "p2": []},
    "checklist": [{"section": "资质", "items": ["营业执照"], "redline": False}],
    "key_info": {
        "timeline": [],
        "qualification": [],
        "commercial": [],
        "technical": [],
    },
    "strategy": {"advantage": "优势", "risk_avoid": "规避", "price": "报价"},
    "scoring": [],
}


def test_parses_minimal_payload():
    data = InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)
    assert data.meta.title.startswith("项目")
    assert data.risks[0].level == "high"


def test_rejects_invalid_risk_level():
    bad = {
        **MINIMAL_PAYLOAD,
        "risks": [{"level": "critical", "title": "x", "desc": "y"}],
    }
    with pytest.raises(ValidationError):
        InterpretHtmlReportData.model_validate(bad)
