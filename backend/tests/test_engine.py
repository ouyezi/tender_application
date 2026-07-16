import pytest

from app.engine.mock import MockEngine


@pytest.mark.asyncio
async def test_mock_engine_returns_fields():
    engine = MockEngine(delay_seconds=0)
    item = {
        "id": 1,
        "title": "企业资质核验",
        "technique": "对照要求",
        "content_mode": "description",
        "content_text": "所有资质文件",
        "importance": "high",
    }
    result = await engine.diagnose_item(task_id="T-1", config_item=item, documents={})
    assert result.content_title == "企业资质核验"
    assert result.result in ("通过", "风险", "缺失")
    assert result.evidence
    assert result.suggestion is not None
