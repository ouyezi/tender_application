#!/usr/bin/env python3
"""Real-file API E2E for tender diagnosis flow (with artifact capture).

Prerequisites:
  - startup.py running (API on --base-url)
  - config.local.json with Agent OS
  - tender_batch_diagnosis_app published

Example:
  .venv/bin/python scripts/e2e_diagnosis_flow.py \\
    --tender uploads/T-20260716-005/tender.docx \\
    --bid uploads/T-20260716-005/bid.docx
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import httpx

from e2e_diagnosis_observability import (
    build_findings_markdown,
    build_results_summary,
    build_stage_durations,
    compute_bid_index_ready_at,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TENDER = ROOT / "uploads" / "T-20260716-005" / "tender.docx"
DEFAULT_BID = ROOT / "uploads" / "T-20260716-005" / "bid.docx"
TERMINAL = {"completed", "failed", "stopped"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_index_status(client: httpx.Client, task_id: str) -> dict | None:
    try:
        resp = client.get(f"/api/workspaces/{task_id}/knowledge/index-status")
        if resp.status_code != 200:
            return None
        return resp.json()
    except httpx.HTTPError:
        return None


def _flush_artifacts(
    *,
    art_dir: Path,
    meta: dict,
    timeline_samples: list,
    index_samples: list,
    detail: dict,
    checklist: dict | None,
    summary: dict,
    stages: dict,
    findings_md: str,
    report_bytes: bytes | None,
) -> None:
    art_dir.mkdir(parents=True, exist_ok=True)
    write_json(art_dir / "meta.json", meta)
    write_json(
        art_dir / "timeline.json",
        {
            "samples": timeline_samples,
            "stages": stages,
            "bid_index_ready_at": compute_bid_index_ready_at(index_samples),
            "index_samples": index_samples,
        },
    )
    write_json(art_dir / "task_final.json", detail)
    write_json(art_dir / "index_status.json", {"samples": index_samples})
    write_json(art_dir / "results_summary.json", summary)
    if checklist is not None:
        write_json(art_dir / "checklist.json", checklist)
    interpret = detail.get("interpret_markdown") or ""
    if interpret:
        (art_dir / "interpret.md").write_text(interpret, encoding="utf-8")
    if report_bytes is not None:
        (art_dir / "report.docx").write_bytes(report_bytes)
    (art_dir / "findings.md").write_text(findings_md, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    parser.add_argument("--tender", type=Path, default=DEFAULT_TENDER)
    parser.add_argument("--bid", type=Path, default=DEFAULT_BID)
    parser.add_argument("--timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ROOT / "artifacts" / "e2e",
    )
    parser.add_argument("--index-sample-every", type=int, default=6)
    args = parser.parse_args()

    if not args.tender.is_file() or not args.bid.is_file():
        print(f"missing files: {args.tender} / {args.bid}", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    upload_timeout = httpx.Timeout(
        connect=30.0,
        read=120.0,
        write=max(3600.0, args.timeout_seconds),
        pool=30.0,
    )
    poll_timeout = httpx.Timeout(120.0)
    started_at = _iso_now()
    t0 = time.time()
    timeline_samples: list[dict] = []
    index_samples: list[dict] = []
    last_status: str | None = None
    poll_i = 0
    detail: dict = {}
    task_id = ""
    upload_seconds = 0.0
    exit_code = 1
    checklist = None
    report_bytes = None
    items: list = []
    results: list = []

    try:
        with httpx.Client(base_url=base, timeout=upload_timeout) as client:
            upload_started = time.time()
            with args.tender.open("rb") as tf, args.bid.open("rb") as bf:
                resp = client.post(
                    "/api/tasks",
                    files={
                        "tender_file": (args.tender.name, tf),
                        "bid_file": (args.bid.name, bf),
                    },
                    data={"background": "", "requirements": ""},
                )
            upload_seconds = time.time() - upload_started
            resp.raise_for_status()
            task = resp.json()
            task_id = task["id"]
            print(f"created task {task_id} upload_seconds={upload_seconds:.1f}")

            art_dir = args.artifacts_dir / task_id
            client.timeout = poll_timeout
            deadline = time.time() + args.timeout_seconds
            detail = task
            timed_out = False

            while time.time() < deadline:
                detail = client.get(f"/api/tasks/{task_id}").json()
                status = detail.get("status")
                sample = {
                    "t": time.time(),
                    "status": status,
                    "failure_stage": detail.get("failure_stage"),
                    "progress_done": detail.get("progress_done"),
                    "progress_total": detail.get("progress_total"),
                }
                timeline_samples.append(sample)
                poll_i += 1

                status_changed = status != last_status
                if status_changed:
                    print(
                        f"STATUS -> {status} stage={detail.get('failure_stage')} "
                        f"progress={detail.get('progress_done')}/{detail.get('progress_total')}"
                    )
                else:
                    print(
                        f"status={status} stage={detail.get('failure_stage')} "
                        f"progress={detail.get('progress_done')}/{detail.get('progress_total')}"
                    )

                should_sample_index = status_changed or (
                    poll_i % max(1, args.index_sample_every) == 0
                )
                if last_status != "diagnosing" and status == "diagnosing":
                    should_sample_index = True
                if last_status == "diagnosing" and status != "diagnosing":
                    should_sample_index = True
                if should_sample_index:
                    idx = _fetch_index_status(client, task_id)
                    if idx is not None:
                        index_samples.append({"t": time.time(), **idx})

                last_status = status
                if status in TERMINAL:
                    break
                time.sleep(args.poll_seconds)
            else:
                timed_out = True
                print("e2e timeout waiting for terminal status", file=sys.stderr)

            # Best-effort checklist even on failure
            try:
                c_resp = client.get(f"/api/tasks/{task_id}/checklist")
                if c_resp.status_code == 200:
                    checklist = c_resp.json()
                    items = [
                        item
                        for cat in checklist.get("categories", [])
                        for item in cat.get("items", [])
                    ]
            except httpx.HTTPError:
                pass

            results = detail.get("results") or []

            if timed_out:
                exit_code = 1
            elif detail.get("status") != "completed":
                print(
                    f"FAILED status={detail.get('status')} "
                    f"failure_stage={detail.get('failure_stage')} "
                    f"error={detail.get('error_message')}",
                    file=sys.stderr,
                )
                exit_code = 1
            elif not items:
                print("checklist empty", file=sys.stderr)
                exit_code = 1
            elif len(results) < 1:
                print("results empty", file=sys.stderr)
                exit_code = 1
            else:
                file_items = [
                    i
                    for i in items
                    if (i.get("diagnosis_mode") or "file") != "offline"
                ]
                mock_hit = False
                if file_items:
                    for row in results:
                        evidence = str(row.get("evidence") or "")
                        if "mock evidence for checklist item" in evidence.lower():
                            print("mock evidence detected in results", file=sys.stderr)
                            mock_hit = True
                            break
                if mock_hit:
                    exit_code = 1
                else:
                    report = client.get(f"/api/tasks/{task_id}/report.docx")
                    if report.status_code != 200:
                        print(
                            f"report.docx status {report.status_code}",
                            file=sys.stderr,
                        )
                        exit_code = 1
                    else:
                        report_bytes = report.content
                        if not report_bytes:
                            print("report.docx empty", file=sys.stderr)
                            exit_code = 1
                        else:
                            print(
                                f"E2E OK task={task_id} "
                                f"items={len(items)} results={len(results)}"
                            )
                            exit_code = 0
    finally:
        ended_at = _iso_now()
        t_end = time.time()
        if task_id:
            art_dir = args.artifacts_dir / task_id
            stages = build_stage_durations(
                timeline_samples,
                upload_seconds=upload_seconds,
                ended_at=t_end,
            )
            ready_at = compute_bid_index_ready_at(index_samples)
            if ready_at is not None:
                stages["bid_index_ready_at"] = ready_at
                if timeline_samples:
                    stages["bid_index_ready_seconds_from_start"] = (
                        ready_at - timeline_samples[0]["t"]
                    )
            summary = build_results_summary(items, results)
            meta = {
                "task_id": task_id,
                "base_url": base,
                "tender": str(args.tender),
                "bid": str(args.bid),
                "started_at": started_at,
                "ended_at": ended_at,
                "exit_code": exit_code,
                "elapsed_seconds": t_end - t0,
            }
            findings_md = build_findings_markdown(
                task_id=task_id,
                tender=str(args.tender),
                bid=str(args.bid),
                started_at=started_at,
                ended_at=ended_at,
                final_status=str(detail.get("status") or ""),
                exit_code=exit_code,
                stages=stages,
                summary=summary,
                failure_stage=detail.get("failure_stage"),
                error_message=detail.get("error_message"),
                artifacts_dir=str(art_dir),
            )
            _flush_artifacts(
                art_dir=art_dir,
                meta=meta,
                timeline_samples=timeline_samples,
                index_samples=index_samples,
                detail=detail,
                checklist=checklist,
                summary=summary,
                stages=stages,
                findings_md=findings_md,
                report_bytes=report_bytes,
            )
            print(f"artifacts written to {art_dir}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
