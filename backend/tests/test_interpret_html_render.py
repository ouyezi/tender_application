from app.interpret_html_schema import InterpretHtmlReportData
from app.templates.interpret_html_report import render_interpret_html_report
from tests.test_interpret_html_schema import MINIMAL_PAYLOAD


def test_render_includes_doctype_title_and_risk_section():
    data = InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)
    html = render_interpret_html_report(data, task_id="T-1")
    assert html.startswith("<!DOCTYPE html>")
    assert "项目 — 招标分析报告" in html
    assert "风险雷达" in html
    assert "风险" in html
    assert "toggleCard" in html
    assert "progressFill" in html


def test_render_escapes_html_injection():
    payload = {
        **MINIMAL_PAYLOAD,
        "risks": [{"level": "low", "title": "<script>alert(1)</script>", "desc": "ok"}],
    }
    data = InterpretHtmlReportData.model_validate(payload)
    html = render_interpret_html_report(data, task_id="T-1")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_redline_checklist_class():
    payload = {
        **MINIMAL_PAYLOAD,
        "checklist": [{"section": "红线", "items": ["废标项"], "redline": True}],
    }
    data = InterpretHtmlReportData.model_validate(payload)
    html = render_interpret_html_report(data, task_id="T-1")
    assert "check-item redline" in html
