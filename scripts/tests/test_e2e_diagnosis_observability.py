from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2e_diagnosis_observability import (
    build_findings_markdown,
    build_results_summary,
    build_stage_durations,
    compute_bid_index_ready_at,
    write_json,
)


def test_build_stage_durations_uses_first_seen_status():
    samples = [
        {"t": 100.0, "status": "interpreting", "progress_done": 0, "progress_total": 0},
        {"t": 110.0, "status": "interpreting", "progress_done": 0, "progress_total": 0},
        {"t": 200.0, "status": "generating_checklist", "progress_done": 0, "progress_total": 0},
        {"t": 350.0, "status": "diagnosing", "progress_done": 1, "progress_total": 10},
        {"t": 500.0, "status": "completed", "progress_done": 10, "progress_total": 10},
    ]
    stages = build_stage_durations(samples, upload_seconds=12.5, ended_at=500.0)
    assert stages["upload_seconds"] == 12.5
    assert stages["by_status"]["interpreting"] == pytest.approx(100.0)
    assert stages["by_status"]["generating_checklist"] == pytest.approx(150.0)
    assert stages["by_status"]["diagnosing"] == pytest.approx(150.0)
    assert stages["total_seconds"] == pytest.approx(400.0)


def test_build_results_summary_counts_and_mock():
    items = [
        {"id": "a", "diagnosis_mode": "file"},
        {"id": "b", "diagnosis_mode": "offline"},
        {"id": "c"},
    ]
    results = [
        {"checklist_item_id": "a", "compliance_status": "satisfied", "evidence": "real"},
        {
            "checklist_item_id": "c",
            "compliance_status": "violated",
            "evidence": "Mock evidence for checklist item c",
        },
    ]
    summary = build_results_summary(items, results)
    assert summary["item_count"] == 3
    assert summary["file_item_count"] == 2
    assert summary["offline_item_count"] == 1
    assert summary["result_count"] == 2
    assert summary["compliance_counts"]["satisfied"] == 1
    assert summary["compliance_counts"]["violated"] == 1
    assert summary["mock_evidence_detected"] is True


def test_compute_bid_index_ready_at_from_index_samples():
    samples = [
        {
            "t": 10.0,
            "files": [
                {"label": "bid", "status": "running"},
                {"label": "tender", "status": "ready"},
            ],
        },
        {
            "t": 40.0,
            "files": [
                {"label": "bid", "status": "ready"},
                {"label": "tender", "status": "ready"},
            ],
        },
    ]
    assert compute_bid_index_ready_at(samples) == 40.0


def test_write_json_and_findings_skeleton(tmp_path: Path):
    out = tmp_path / "meta.json"
    write_json(out, {"task_id": "T-1"})
    assert json.loads(out.read_text(encoding="utf-8"))["task_id"] == "T-1"

    md = build_findings_markdown(
        task_id="T-1",
        tender="uploads/tender.docx",
        bid="uploads/bid.docx",
        started_at="2026-07-20T00:00:00+00:00",
        ended_at="2026-07-20T01:00:00+00:00",
        final_status="failed",
        exit_code=1,
        stages={"upload_seconds": 1.0, "by_status": {"diagnosing": 10.0}, "total_seconds": 11.0},
        summary={"item_count": 0, "result_count": 0, "mock_evidence_detected": False},
        failure_stage="diagnosing",
        error_message="bid index timeout",
        artifacts_dir=str(tmp_path),
    )
    assert "T-1" in md
    assert "bid index timeout" in md
    assert "P0" in md or "问题清单" in md
