from __future__ import annotations

import io

import pytest

from app import db
from app.models import DiagnosisTask
from app.services.execution_graph import get_tracker
from app.services.execution_graph.query import BID_RETRIEVAL_CHILD_KEYS


def _pdf_bytes():
    return b"%PDF-1.4 fake"


async def _create_task(client):
    files = {
        "tender_file": ("tender.pdf", io.BytesIO(_pdf_bytes()), "application/pdf"),
        "bid_file": (
            "bid.docx",
            io.BytesIO(b"PK fake"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    }
    r = await client.post("/api/tasks", data={"background": "x"}, files=files)
    assert r.status_code == 201
    return r.json()["id"]


@pytest.mark.asyncio
async def test_execution_graph_not_found(client):
    r = await client.get("/api/tasks/T-NOPE/execution-graph")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_execution_graph_after_create(client):
    task_id = await _create_task(client)
    r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == task_id
    assert body["legacy"] is False
    keys = {n["key"] for n in body["nodes"]}
    assert "parse.tender" in keys
    assert "bid.retrieval" in keys
    assert "diagnosis" in keys
    parse_tender = next(n for n in body["nodes"] if n["key"] == "parse.tender")
    assert parse_tender["status"] in ("pending", "running")
    assert body["summary"]["total_nodes"] < 20


@pytest.mark.asyncio
async def test_execution_graph_rollups_bid_retrieval(client):
    task_id = await _create_task(client)
    tracker = get_tracker(task_id)
    async with tracker.track("parse.bid"):
        pass
    r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    assert r.status_code == 200
    body = r.json()
    container = next(n for n in body["nodes"] if n["key"] == "bid.retrieval")
    assert container["status"] == "pending"
    top_level_keys = {n["key"] for n in body["nodes"] if not n.get("parent_key")}
    assert "parse.bid" not in top_level_keys
    assert "index.segments" not in top_level_keys
    assert "bid.retrieval" in top_level_keys


@pytest.mark.asyncio
async def test_execution_graph_rollups_bid_retrieval_completed(client):
    task_id = await _create_task(client)
    tracker = get_tracker(task_id)
    for key in BID_RETRIEVAL_CHILD_KEYS:
        async with tracker.track(key):
            pass
    r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    body = r.json()
    container = next(n for n in body["nodes"] if n["key"] == "bid.retrieval")
    assert container["status"] == "completed"
    assert container["duration_ms"] is not None


@pytest.mark.asyncio
async def test_execution_graph_legacy_empty(client):
    task_id = "T-LEGACY-001"
    async with db.SessionLocal() as session:
        session.add(
            DiagnosisTask(
                id=task_id,
                tender_filename="t.pdf",
                tender_path="/tmp/t.pdf",
                bid_filename="b.docx",
                bid_path="/tmp/b.docx",
                status="completed",
            )
        )
        await session.commit()
    r = await client.get(f"/api/tasks/{task_id}/execution-graph")
    assert r.status_code == 200
    body = r.json()
    assert body["legacy"] is True
    assert body["nodes"] == []
