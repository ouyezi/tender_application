from __future__ import annotations

import io

import pytest
from httpx import AsyncClient

from app.services import scheduler
from app.services.report import build_markdown, write_docx


def test_build_markdown_contains_titles():
    results = [
        {
            "content_title": "资质",
            "description": "d",
            "result": "风险",
            "evidence": "e",
            "suggestion": "s",
        },
    ]
    md = build_markdown("T-1", results)
    assert "# 标书诊断报告" in md
    assert "资质" in md


def test_write_docx_creates_non_empty_file(tmp_path):
    path = tmp_path / "r.docx"
    write_docx(str(path), "# 标书诊断报告\n\n你好")
    assert path.exists() and path.stat().st_size > 0


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _seed_configs(client: AsyncClient, n: int = 2) -> None:
    for i in range(n):
        r = await client.post(
            "/api/configs",
            json={
                "title": f"检查项{i + 1}",
                "technique": "对照",
                "content_mode": "description",
                "content_text": f"内容{i + 1}",
                "importance": "medium",
            },
        )
        assert r.status_code == 201


async def _create_task(client: AsyncClient) -> dict:
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": (
            "bid.docx",
            io.BytesIO(b"PK fake"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    r = await client.post(
        "/api/tasks",
        data={"background": "bg", "requirements": "req"},
        files=files,
    )
    assert r.status_code == 201
    return r.json()


@pytest.mark.asyncio
async def test_interpret_html_available_after_interpret(client):
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "completed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert "# 招标文件解读报告" in detail["interpret_markdown"]

    r = await client.get(f"/api/tasks/{task_id}/interpret.html")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "招标文件解读报告" in r.text


@pytest.mark.asyncio
async def test_completed_task_report_download(client):
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "completed"

    detail = (await client.get(f"/api/tasks/{task_id}")).json()
    assert detail["report_md_path"]
    assert detail["report_docx_path"]
    assert "# 标书诊断报告" in detail["report_markdown"]

    r = await client.get(f"/api/tasks/{task_id}/report.docx")
    assert r.status_code == 200
    assert len(r.content) > 0


@pytest.mark.asyncio
async def test_stopped_task_report_download_404(client):
    await _seed_configs(client, 2)
    body = await _create_task(client)
    task_id = body["id"]

    r = await client.post(f"/api/tasks/{task_id}/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "stopped"

    status = await scheduler.wait_for_terminal(task_id, timeout=5)
    assert status == "stopped"

    r = await client.get(f"/api/tasks/{task_id}/report.docx")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_task_files_download(client):
    await _seed_configs(client, 1)
    body = await _create_task(client)
    task_id = body["id"]
    await scheduler.wait_for_terminal(task_id, timeout=5)

    tender = await client.get(f"/api/tasks/{task_id}/files/tender")
    assert tender.status_code == 200
    assert tender.content.startswith(b"%PDF")

    bid = await client.get(f"/api/tasks/{task_id}/files/bid")
    assert bid.status_code == 200
    assert len(bid.content) > 0

    bad = await client.get(f"/api/tasks/{task_id}/files/other")
    assert bad.status_code == 404
