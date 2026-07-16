from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, DiagnosisResult, DiagnosisTask
from app.services import artifact, scheduler
from app.services.report import build_markdown, generate_and_save_reports, write_docx


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


@pytest.mark.asyncio
async def test_generate_and_save_reports_syncs_to_artifact(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    monkeypatch.setattr("app.services.report.REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(artifact, "UPLOAD_DIR", tmp_path / "uploads")
    (tmp_path / "reports").mkdir()
    (tmp_path / "uploads").mkdir()

    task_id = "T-SYNC-001"
    async with session_factory() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="tender.pdf",
                tender_path="/uploads/tender.pdf",
                bid_filename="bid.pdf",
                bid_path="/uploads/bid.pdf",
                status="completed",
            )
        )
        session.add(
            DiagnosisResult(
                task_id=task_id,
                content_title="资质",
                description="d",
                result="风险",
                evidence="e",
                suggestion="s",
                sort_order=0,
            )
        )
        await session.commit()

    md_path, docx_path = await generate_and_save_reports(task_id, session_factory=session_factory)
    assert Path(md_path).is_file()
    assert Path(docx_path).is_file()

    artifact_report = tmp_path / "uploads" / task_id / "report"
    assert (artifact_report / "report.md").is_file()
    assert (artifact_report / "report.docx").is_file()
    assert (artifact_report / "report.md").read_text(encoding="utf-8") == Path(md_path).read_text(
        encoding="utf-8"
    )

    await engine.dispose()


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
async def test_completed_task_report_download(client, tmp_path):
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

    artifact_report = tmp_path / "uploads" / task_id / "report"
    assert (artifact_report / "report.md").is_file()
    assert (artifact_report / "report.docx").is_file()
    assert (artifact_report / "interpret.md").is_file()
    assert (artifact_report / "interpret.html").is_file()


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
