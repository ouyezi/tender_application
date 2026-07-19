import pytest

from app.engine.interpretation_mock import MockInterpretationAgent


@pytest.mark.asyncio
async def test_mock_interpretation_returns_markdown_with_sections():
    agent = MockInterpretationAgent(delay_seconds=0)
    result = await agent.interpret(
        task_id="T-20260716-001",
        tender_text="市政工程招标正文",
        background="市政工程",
        requirements="重点关注废标条款",
    )
    assert result.title == "招标文件解读报告"
    assert "# 招标文件解读报告" in result.markdown
    for heading in ("项目概况", "招标范围与资质要求", "评分办法要点", "废标/否决条款摘要", "风险提示"):
        assert heading in result.markdown
    assert "T-20260716-001" in result.markdown
    assert "市政工程" in result.markdown
