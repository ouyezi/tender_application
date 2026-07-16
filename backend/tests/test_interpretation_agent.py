import pytest

from app.engine.interpretation_mock import MockInterpretationAgent


@pytest.mark.asyncio
async def test_mock_interpretation_returns_markdown_with_sections(tmp_path):
    agent = MockInterpretationAgent(delay_seconds=0)
    tender = tmp_path / "tender.pdf"
    tender.write_bytes(b"%PDF-1.4")
    result = await agent.interpret(
        task_id="T-20260716-001",
        tender_path=str(tender),
        background="市政工程",
    )
    assert result.title == "招标文件解读报告"
    assert "# 招标文件解读报告" in result.markdown
    for heading in ("项目概况", "招标范围与资质要求", "评分办法要点", "废标/否决条款摘要", "风险提示"):
        assert heading in result.markdown
    assert "tender.pdf" in result.markdown
