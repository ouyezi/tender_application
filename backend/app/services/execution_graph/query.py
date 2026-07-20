from __future__ import annotations

import json
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DiagnosisTask, ExecutionEdge, ExecutionNode, utcnow

TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped"})


def _parse_meta(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _live_duration_ms(started_at) -> int:
    now = utcnow()
    started = started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return max(0, int((now - started).total_seconds() * 1000))


def _node_duration_ms(node: ExecutionNode) -> int:
    if node.status == "running" and node.started_at is not None:
        return _live_duration_ms(node.started_at)
    return node.duration_ms or 0


async def build_execution_graph_response(
    session: AsyncSession, task: DiagnosisTask
) -> dict:
    nodes_result = await session.execute(
        select(ExecutionNode)
        .where(ExecutionNode.task_id == task.id)
        .order_by(ExecutionNode.sort_order, ExecutionNode.node_key)
    )
    nodes = list(nodes_result.scalars().all())

    edges_result = await session.execute(
        select(ExecutionEdge).where(ExecutionEdge.task_id == task.id)
    )
    edges = list(edges_result.scalars().all())

    legacy = len(nodes) == 0
    status_counts = {"completed": 0, "running": 0, "failed": 0, "pending": 0}
    for node in nodes:
        if node.status in status_counts:
            status_counts[node.status] += 1

    total_duration_ms = sum(_node_duration_ms(node) for node in nodes)

    node_out = []
    for node in nodes:
        duration_ms: int | None
        if node.status == "running" and node.started_at is not None:
            duration_ms = _live_duration_ms(node.started_at)
        else:
            duration_ms = node.duration_ms

        node_out.append(
            {
                "id": node.id,
                "key": node.node_key,
                "label": node.label,
                "kind": node.kind,
                "status": node.status,
                "parent_key": node.parent_key,
                "started_at": node.started_at,
                "ended_at": node.ended_at,
                "duration_ms": duration_ms,
                "meta": _parse_meta(node.meta),
            }
        )

    edge_out = [
        {
            "from": edge.from_key,
            "to": edge.to_key,
            "kind": edge.edge_kind,
        }
        for edge in edges
    ]

    return {
        "task_id": task.id,
        "task_status": task.status,
        "is_terminal": task.status in TERMINAL_TASK_STATUSES,
        "legacy": legacy,
        "summary": {
            "total_nodes": len(nodes),
            "completed": status_counts["completed"],
            "running": status_counts["running"],
            "failed": status_counts["failed"],
            "pending": status_counts["pending"],
            "total_duration_ms": total_duration_ms,
        },
        "nodes": node_out,
        "edges": edge_out,
    }
