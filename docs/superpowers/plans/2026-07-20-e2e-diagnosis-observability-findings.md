# E2E Diagnosis Observability and Findings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enhance the real-file diagnosis E2E script to capture stage timelines, dump API artifacts, auto-skeleton a findings report, run the full T-20260716-005 flow, and complete the findings list — without changing business/backend code.

**Architecture:** Extract pure observability helpers into `scripts/e2e_diagnosis_observability.py` (timeline samples, stage durations, results summary, findings markdown). Wire them into `scripts/e2e_diagnosis_flow.py` so every poll writes samples, index-status is sampled on a cadence, and terminal runs always flush an `artifacts/e2e/<task_id>/` evidence pack. Unit-test helpers with pytest; the long real-file run is a manual/ops step that fills `findings.md`.

**Tech Stack:** Python 3.11, httpx, argparse, pytest, existing FastAPI task/workspace/knowledge APIs.

**Spec:** `docs/superpowers/specs/2026-07-20-e2e-diagnosis-observability-findings-design.md`

---

## File Structure

```text
scripts/
  e2e_diagnosis_observability.py   # NEW pure helpers (no httpx)
  e2e_diagnosis_flow.py            # MODIFY wire artifacts + sampling
  tests/
    test_e2e_diagnosis_observability.py  # NEW unit tests

.gitignore                         # MODIFY ignore artifacts/

artifacts/e2e/<task_id>/           # runtime only (gitignored)
```

**执行顺序：** Task 1–4 可快速合入；Task 5 需已启动的 `startup.py` 与 Agent OS，可能数小时；Task 6 依赖 Task 5 产物。

---

### Task 1: gitignore `artifacts/`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add ignore rule**

在 `.gitignore` 末尾追加：

```gitignore

# E2E evidence packs (may include large report.docx)
artifacts/
```

- [ ] **Step 2: Verify git ignores the path**

Run:

```bash
mkdir -p artifacts/e2e/_probe && touch artifacts/e2e/_probe/x && git check-ignore -v artifacts/e2e/_probe/x && rm -rf artifacts/e2e/_probe
```

Expected: 打印一条匹配 `.gitignore` 中 `artifacts/` 的规则。

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: gitignore E2E artifacts directory"
```

---

### Task 2: Observability helpers + unit tests

**Files:**
- Create: `scripts/e2e_diagnosis_observability.py`
- Create: `scripts/tests/test_e2e_diagnosis_observability.py`
- Create: `scripts/tests/__init__.py`（空文件，便于 pytest 收集）

- [ ] **Step 1: Write failing tests**

创建 `scripts/tests/__init__.py`（空）。

创建 `scripts/tests/test_e2e_diagnosis_observability.py`：

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application && \
  PYTHONPATH=scripts .venv/bin/python -m pytest scripts/tests/test_e2e_diagnosis_observability.py -v
```

Expected: FAIL（`ModuleNotFoundError: e2e_diagnosis_observability` 或 import 失败）

- [ ] **Step 3: Implement helpers**

创建 `scripts/e2e_diagnosis_observability.py`：

```python
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def build_stage_durations(
    samples: list[dict[str, Any]],
    *,
    upload_seconds: float,
    ended_at: float,
) -> dict[str, Any]:
    """Durations from first-seen status timestamps to next first-seen / ended_at."""
    first_seen: dict[str, float] = {}
    for sample in samples:
        status = sample.get("status")
        if not status or status in first_seen:
            continue
        first_seen[status] = float(sample["t"])

    ordered = sorted(first_seen.items(), key=lambda kv: kv[1])
    by_status: dict[str, float] = {}
    for idx, (status, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else float(ended_at)
        by_status[status] = max(0.0, end - start)

    total = 0.0
    if ordered:
        total = max(0.0, float(ended_at) - ordered[0][1])
    return {
        "upload_seconds": float(upload_seconds),
        "by_status": by_status,
        "first_seen": first_seen,
        "total_seconds": total,
    }


def build_results_summary(
    items: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    file_items = [i for i in items if (i.get("diagnosis_mode") or "file") != "offline"]
    offline_items = [i for i in items if (i.get("diagnosis_mode") or "file") == "offline"]
    compliance_counts = Counter(
        str(r.get("compliance_status") or r.get("result") or "unknown") for r in results
    )
    mock = any(
        "mock evidence for checklist item" in str(r.get("evidence") or "").lower()
        for r in results
    )
    return {
        "item_count": len(items),
        "file_item_count": len(file_items),
        "offline_item_count": len(offline_items),
        "result_count": len(results),
        "compliance_counts": dict(compliance_counts),
        "mock_evidence_detected": mock,
    }


def compute_bid_index_ready_at(index_samples: list[dict[str, Any]]) -> float | None:
    """First sample time where a file labeled bid (case-insensitive) is ready."""
    for sample in index_samples:
        files = sample.get("files") or []
        for f in files:
            label = str(f.get("label") or "").lower()
            if label == "bid" and str(f.get("status") or "").lower() == "ready":
                return float(sample["t"])
    return None


def build_findings_markdown(
    *,
    task_id: str,
    tender: str,
    bid: str,
    started_at: str,
    ended_at: str,
    final_status: str,
    exit_code: int,
    stages: dict[str, Any],
    summary: dict[str, Any],
    failure_stage: str | None,
    error_message: str | None,
    artifacts_dir: str,
) -> str:
    by_status = stages.get("by_status") or {}
    rows = "\n".join(
        f"| `{name}` | {seconds:.1f}s |" for name, seconds in by_status.items()
    )
    if not rows:
        rows = "| _(none)_ | 0.0s |"
    problems = []
    if exit_code != 0:
        problems.append(
            f"- **P0** 终态非成功：`status={final_status}` "
            f"`failure_stage={failure_stage}` `error={error_message}`"
        )
    if summary.get("mock_evidence_detected"):
        problems.append("- **P0** 结果中检测到 mock evidence")
    if not problems:
        problems.append("- （跑通后在此补充 P0/P1/P2 问题；证据指向本目录文件）")

    return f"""# E2E Findings — {task_id}

## 1. Run 摘要

| 字段 | 值 |
|---|---|
| task_id | `{task_id}` |
| tender | `{tender}` |
| bid | `{bid}` |
| started_at | {started_at} |
| ended_at | {ended_at} |
| final_status | `{final_status}` |
| exit_code | {exit_code} |
| artifacts | `{artifacts_dir}` |
| failure_stage | `{failure_stage}` |
| error_message | {error_message or ""} |

## 2. 阶段耗时表

| 阶段 | 耗时 |
|---|---|
| upload | {float(stages.get("upload_seconds") or 0):.1f}s |
{rows}
| **total (from first status)** | **{float(stages.get("total_seconds") or 0):.1f}s** |

## 3. 产出抽检

```json
{json.dumps(summary, ensure_ascii=False, indent=2)}
```

## 4. 问题清单

{chr(10).join(problems)}

## 5. 优化建议

- （本期只记录：性能 / 提示词 / Agent 契约 / 检索等；不改业务代码）

## 6. 后续动作

- 对 P0/P1 另开 spec/plan 再实施业务修复
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application && \
  PYTHONPATH=scripts .venv/bin/python -m pytest scripts/tests/test_e2e_diagnosis_observability.py -v
```

Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add scripts/e2e_diagnosis_observability.py \
  scripts/tests/__init__.py \
  scripts/tests/test_e2e_diagnosis_observability.py
git commit -m "test: add E2E diagnosis observability helpers"
```

---

### Task 3: Wire artifacts into `e2e_diagnosis_flow.py`

**Files:**
- Modify: `scripts/e2e_diagnosis_flow.py`

- [ ] **Step 1: Replace script with observability-wired version**

将 `scripts/e2e_diagnosis_flow.py` 替换为以下完整内容（保留长 upload timeout 与现有断言）：

```python
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
    # Allow `python scripts/e2e_diagnosis_flow.py` without installing package
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
```

注意：`sys.path.insert` 必须在 `from e2e_diagnosis_observability import ...` **之前**。把 path insert 挪到文件顶部 import 区：

将文件开头的 import 段改为：

```python
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
```

并删除 `if __name__` 里重复的 `sys.path.insert`，保留：

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Dry-run help + missing files**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application && \
  .venv/bin/python scripts/e2e_diagnosis_flow.py --help && \
  .venv/bin/python scripts/e2e_diagnosis_flow.py --tender /no/such.docx --bid /no/such.docx ; echo exit=$?
```

Expected: 打印帮助；缺文件时 stderr 含 `missing files`，`exit=2`。

- [ ] **Step 3: Re-run helper unit tests**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application && \
  PYTHONPATH=scripts .venv/bin/python -m pytest scripts/tests/test_e2e_diagnosis_observability.py -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add scripts/e2e_diagnosis_flow.py
git commit -m "feat: capture E2E diagnosis artifacts and stage timeline"
```

---

### Task 4: 文档交叉引用（轻量）

**Files:**
- Modify: `docs/superpowers/plans/2026-07-19-real-batch-diagnosis-and-e2e.md`（仅在 Task 6 Step 4 旁加一行指向本计划；若该文件本地有无关脏改动则跳过本 Task，改在 README 一句说明）

- [ ] **Step 1: 在旧计划 Task 6 Step 4 后追加指针**

在 `### Task 6` 的 Step 4 代码块后追加：

```markdown

> **Observability follow-up:** 完整跑通的取证与 findings 见
> `docs/superpowers/plans/2026-07-20-e2e-diagnosis-observability-findings.md`
> （spec: `docs/superpowers/specs/2026-07-20-e2e-diagnosis-observability-findings-design.md`）。
```

若 `git diff` 显示该文件还有其它无关修改，**不要**把无关改动一并提交；可改为只在本计划 header 保留双向链接（本文件已有 Spec 链接即可），跳过本 step 的 commit。

- [ ] **Step 2: Commit（仅当只含上述指针时）**

```bash
git add docs/superpowers/plans/2026-07-19-real-batch-diagnosis-and-e2e.md
git commit -m "docs: link batch diagnosis E2E to observability follow-up"
```

---

### Task 5: 跑完整真实文件 E2E

**Files:**
- Runtime only: `artifacts/e2e/<task_id>/`（不提交）

**前置：**
- Terminal A：`.venv/bin/python startup.py` 成功监听 `8888`
- `config.local.json` 可用；`tender_batch_diagnosis_app` 已发布
- 样例文件存在：`uploads/T-20260716-005/tender.docx`、`bid.docx`

- [ ] **Step 1: 健康检查**

Run:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8888/api/tasks
```

Expected: `200`（或该列表接口的其它成功码；非连接失败）

- [ ] **Step 2: 启动 E2E（可能数小时）**

Run:

```bash
cd /Users/tongqianni/xlab/tender_application && \
  .venv/bin/python scripts/e2e_diagnosis_flow.py \
    --tender uploads/T-20260716-005/tender.docx \
    --bid uploads/T-20260716-005/bid.docx \
    --artifacts-dir artifacts/e2e
```

Expected:
- 打印 `created task ...`
- 周期性 `STATUS -> ...` / `status=...`
- 结束时 `artifacts written to artifacts/e2e/<task_id>`
- 成功时 `E2E OK ...` 且退出码 0；失败时非 0 但仍有产物目录

- [ ] **Step 3: 校验产物齐全**

Run（将 `TASK_ID` 换成实际 id）：

```bash
TASK_ID=<paste>
ls -la artifacts/e2e/$TASK_ID
python - <<PY
import json
from pathlib import Path
p = Path("artifacts/e2e") / "$TASK_ID"
for name in ["meta.json", "timeline.json", "task_final.json", "findings.md", "results_summary.json"]:
    assert (p / name).exists(), name
tl = json.loads((p / "timeline.json").read_text())
assert tl.get("samples"), "timeline samples empty"
assert "stages" in tl
print("artifact check OK", p)
print("stages", tl["stages"])
PY
```

Expected: `artifact check OK`；若成功跑通还应有 `checklist.json`、`report.docx`。

- [ ] **Step 4: 记录 task_id 到 findings 骨架头部（无需 commit）**

打开 `artifacts/e2e/<task_id>/findings.md`，确认自动摘要与耗时表非空。完整问题分析在 Task 6。

---

### Task 6: 补全 findings 并择要归档

**Files:**
- Modify (runtime): `artifacts/e2e/<task_id>/findings.md`
- Create (optional archive): `docs/superpowers/notes/2026-07-20-e2e-diagnosis-findings.md`（若目录不存在则创建；**只归档摘要与问题列表，不含 report.docx / 大 JSON**）

- [ ] **Step 1: 根据证据包补全问题清单**

对照：
- `timeline.json` → 异常长阶段、卡在某 status
- `task_final.json` → `failure_stage` / `error_message`
- `results_summary.json` → compliance 分布、mock、file/offline 比
- `index_status.json` → bid 索引是否迟到/失败
- `checklist.json` / `report.docx` → 产出完整性

在 `findings.md` 第 4/5/6 节写入具体条目，每条含：现象、证据文件、影响、优先级（P0/P1/P2）。优化建议只记录不实现。

- [ ] **Step 2: （可选）精简归档到 docs**

创建 `docs/superpowers/notes/2026-07-20-e2e-diagnosis-findings.md`，复制 findings 的摘要表 + 问题清单 + 优化建议（去掉本地绝对路径中的敏感信息如有）。**不要**复制 `report.docx` 或完整 checklist JSON。

- [ ] **Step 3: Commit 归档笔记（若做了 Step 2）**

```bash
git add docs/superpowers/notes/2026-07-20-e2e-diagnosis-findings.md
git commit -m "docs: archive E2E diagnosis run findings summary"
```

若未创建 notes，跳过 commit；保留 gitignored 的 `artifacts/e2e/<task_id>/findings.md` 即可。

---

## Spec Coverage Checklist

| Spec 要求 | Task |
|---|---|
| 只改 E2E/取证，不改业务代码 | Task 1–3（+ 文档 Task 4） |
| `--artifacts-dir` 默认 `artifacts/e2e` | Task 3 |
| timeline + 阶段耗时（first-seen status） | Task 2–3 |
| index-status 每 6 poll + diagnosing 边界 | Task 3 |
| 终态落盘 meta/timeline/task_final/checklist/report/summary/findings | Task 3 |
| 成功断言（completed/checklist/results/mock/report） | Task 3 |
| 失败仍落盘 | Task 3 `finally` |
| `artifacts/` gitignore | Task 1 |
| 真实样例完整跑通 | Task 5 |
| findings 模板与补全 | Task 2 + Task 6 |
| 业务优化只记录不实现 | Task 6 |

## 明确不做（计划内不出现）

- 改 scheduler / 批诊断引擎 / Agent 契约 / 提示词 / 检索
- UI 浏览器自动化
- 提交 1GB 样例或 `artifacts/` 大文件
- 读取服务端本地 log 文件

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-20-e2e-diagnosis-observability-findings.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks
2. **Inline Execution** — execute tasks in this session with executing-plans and checkpoints

Which approach?
