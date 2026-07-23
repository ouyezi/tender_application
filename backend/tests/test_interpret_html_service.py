import asyncio

import pytest

from app.engine.base import InterpretationResult
from app.services import interpret_html_service, interpret_report


@pytest.fixture
def report_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("app.services.interpret_html_service.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr("app.services.artifact.REPORT_DIR", tmp_path / "reports")
    return tmp_path / "reports"


@pytest.mark.asyncio
async def test_start_generation_writes_html(report_dir, monkeypatch):
    interpret_report.save_interpret_reports(
        "T-HTML",
        InterpretationResult(markdown="# 解读报告\n\n内容"),
    )

    async def fake_generate(task_id, interpret_report_text):
        from app.interpret_html_schema import InterpretHtmlReportData
        from tests.test_interpret_html_schema import MINIMAL_PAYLOAD

        return InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)

    monkeypatch.setattr(interpret_html_service, "_generate_data", fake_generate)

    async def fake_persist(task_id, html_path):
        return None

    monkeypatch.setattr(interpret_html_service, "_persist_html_path", fake_persist)

    await interpret_html_service.start("T-HTML")
    for _ in range(100):
        if not interpret_html_service.is_lane_active("T-HTML"):
            break
        await asyncio.sleep(0.01)

    html_path = report_dir / "T-HTML" / "interpret.html"
    assert html_path.is_file()
    assert "招标分析报告" in html_path.read_text(encoding="utf-8")
    assert interpret_html_service.get_error("T-HTML") is None


@pytest.mark.asyncio
async def test_start_raises_conflict_when_lane_active():
    interpret_html_service._set_lane_active_for_test("T-HTML", True)
    try:
        with pytest.raises(interpret_html_service.InterpretHtmlConflict):
            await interpret_html_service.start("T-HTML")
    finally:
        interpret_html_service._set_lane_active_for_test("T-HTML", False)
