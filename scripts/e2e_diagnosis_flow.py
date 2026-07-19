#!/usr/bin/env python3
"""Real-file API E2E for tender diagnosis flow.

Prerequisites:
  - startup.py running (API on --base-url)
  - config.local.json with Agent OS
  - tender_batch_diagnosis_app published

Example:
  .venv/bin/python scripts/e2e_diagnosis_flow.py

When running from a git worktree, sample uploads may live in the main repo.
Pass explicit paths if defaults are missing:

  .venv/bin/python scripts/e2e_diagnosis_flow.py \\
    --tender /Users/tongqianni/xlab/tender_application/uploads/T-20260716-005/tender.docx \\
    --bid /Users/tongqianni/xlab/tender_application/uploads/T-20260716-005/bid.docx
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TENDER = ROOT / "uploads" / "T-20260716-005" / "tender.docx"
DEFAULT_BID = ROOT / "uploads" / "T-20260716-005" / "bid.docx"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8888")
    parser.add_argument("--tender", type=Path, default=DEFAULT_TENDER)
    parser.add_argument("--bid", type=Path, default=DEFAULT_BID)
    parser.add_argument("--timeout-seconds", type=float, default=14400.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    if not args.tender.is_file() or not args.bid.is_file():
        print(f"missing files: {args.tender} / {args.bid}", file=sys.stderr)
        return 2

    base = args.base_url.rstrip("/")
    with httpx.Client(base_url=base, timeout=120.0) as client:
        with args.tender.open("rb") as tf, args.bid.open("rb") as bf:
            resp = client.post(
                "/api/tasks",
                files={
                    "tender_file": (args.tender.name, tf),
                    "bid_file": (args.bid.name, bf),
                },
                data={"background": "", "requirements": ""},
            )
        resp.raise_for_status()
        task = resp.json()
        task_id = task["id"]
        print(f"created task {task_id}")

        deadline = time.time() + args.timeout_seconds
        detail = task
        while time.time() < deadline:
            detail = client.get(f"/api/tasks/{task_id}").json()
            status = detail.get("status")
            print(
                f"status={status} stage={detail.get('failure_stage')} "
                f"progress={detail.get('progress_done')}/{detail.get('progress_total')}"
            )
            if status in {"completed", "failed", "stopped"}:
                break
            time.sleep(args.poll_seconds)
        else:
            print("e2e timeout waiting for terminal status", file=sys.stderr)
            return 1

        if detail.get("status") != "completed":
            print(
                f"FAILED status={detail.get('status')} "
                f"failure_stage={detail.get('failure_stage')} "
                f"error={detail.get('error_message')}",
                file=sys.stderr,
            )
            return 1

        checklist = client.get(f"/api/tasks/{task_id}/checklist").json()
        items = [
            item
            for cat in checklist.get("categories", [])
            for item in cat.get("items", [])
        ]
        if not items:
            print("checklist empty", file=sys.stderr)
            return 1

        results = detail.get("results") or []
        if len(results) < 1:
            print("results empty", file=sys.stderr)
            return 1
        file_items = [
            i for i in items if (i.get("diagnosis_mode") or "file") != "offline"
        ]
        if file_items:
            for row in results:
                evidence = str(row.get("evidence") or "")
                if "mock evidence for checklist item" in evidence.lower():
                    print("mock evidence detected in results", file=sys.stderr)
                    return 1

        report = client.get(f"/api/tasks/{task_id}/report.docx")
        if report.status_code != 200:
            print(f"report.docx status {report.status_code}", file=sys.stderr)
            return 1

        print(f"E2E OK task={task_id} items={len(items)} results={len(results)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
