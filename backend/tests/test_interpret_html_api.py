import asyncio

import pytest

from app.services import scheduler
from tests.test_report import _create_task, _seed_configs


@pytest.mark.asyncio
async def test_generate_interpret_html_flow(client, monkeypatch):
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=10)
    assert status == "completed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail.get("interpret_markdown")
    assert not detail.get("interpret_html_path")

    async def fake_generate(task_id, interpret_report_text):
        from app.interpret_html_schema import InterpretHtmlReportData
        from tests.test_interpret_html_schema import MINIMAL_PAYLOAD

        return InterpretHtmlReportData.model_validate(MINIMAL_PAYLOAD)

    monkeypatch.setattr(
        "app.services.interpret_html_service._generate_data",
        fake_generate,
    )

    r = await client.post(f"/api/tasks/{task_id}/actions/generate-interpret-html")
    assert r.status_code == 202

    for _ in range(100):
        readiness = (await client.get(f"/api/tasks/{task_id}/readiness")).json()
        if readiness["interpret_html_ready"]:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("interpret html not ready")

    r2 = await client.get(f"/api/tasks/{task_id}/interpret.html")
    assert r2.status_code == 200
    assert "text/html" in r2.headers.get("content-type", "")
    assert "招标分析报告" in r2.text
