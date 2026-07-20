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
