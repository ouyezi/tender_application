from pathlib import Path

from app.engine.base import InterpretationResult
from app.services.interpret_report import markdown_to_html_document, save_interpret_reports


def test_markdown_to_html_document_wraps_title_and_body():
    html = markdown_to_html_document("招标文件解读报告", "# 标题\n\n正文段落\n")
    assert "<!DOCTYPE html>" in html
    assert "<title>招标文件解读报告</title>" in html
    assert "正文段落" in html


def test_save_interpret_reports_writes_md_and_html(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.interpret_report.REPORT_DIR", tmp_path)
    result = InterpretationResult(markdown="# 招标文件解读报告\n\nhello\n")
    md_path, html_path = save_interpret_reports("T-1", result)
    assert Path(md_path).read_text(encoding="utf-8") == result.markdown
    html = Path(html_path).read_text(encoding="utf-8")
    assert "hello" in html
    assert Path(html_path).name == "interpret.html"
