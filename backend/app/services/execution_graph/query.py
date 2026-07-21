from __future__ import annotations

import json
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DiagnosisTask, ExecutionEdge, ExecutionNode, utcnow

TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "stopped"})
STATUS_PRIORITY = ("failed", "running", "interrupted", "pending", "completed", "skipped")
BID_RETRIEVAL_CHILD_KEYS = (
    "parse.bid",
    "index.segments",
    "index.enrich",
    "index.fts",
    "index.vectors",
    "index.wiki",
    "index.gate",
)


def _parse_meta(raw: str) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ensure_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _live_duration_ms(started_at) -> int:
    now = utcnow()
    started = _ensure_utc(started_at)
    return max(0, int((now - started).total_seconds() * 1000))


def _duration_between(started_at, ended_at) -> int:
    started = _ensure_utc(started_at)
    ended = _ensure_utc(ended_at)
    return max(0, int((ended - started).total_seconds() * 1000))


def _sanitize_node_status(node: ExecutionNode, task_status: str) -> str:
    if (
        task_status in TERMINAL_TASK_STATUSES
        and node.status == "running"
        and node.started_at is not None
    ):
        return "completed"
    return node.status


def _rollup_container_status(child_statuses: list[str]) -> str:
    if not child_statuses:
        return "pending"
    for status in STATUS_PRIORITY:
        if status == "completed":
            if all(s in ("completed", "skipped") for s in child_statuses):
                if all(s == "skipped" for s in child_statuses):
                    return "skipped"
                return "completed"
            continue
        if status in child_statuses:
            return status
    return "pending"


def _rollup_container_times(
    children: list[ExecutionNode],
    rolled_status: str,
) -> tuple:
    started_times = [
        _ensure_utc(c.started_at) for c in children if c.started_at is not None
    ]
    ended_times = [_ensure_utc(c.ended_at) for c in children if c.ended_at is not None]
    started_at = min(started_times) if started_times else None
    ended_at = max(ended_times) if ended_times else None

    duration_ms: int | None
    if rolled_status == "running" and started_at is not None:
        duration_ms = _live_duration_ms(started_at)
    elif started_at is not None and ended_at is not None:
        duration_ms = _duration_between(started_at, ended_at)
    else:
        duration_ms = None
    return started_at, ended_at, duration_ms


def _build_node_dict(
    node: ExecutionNode,
    *,
    status: str,
    started_at,
    ended_at,
    duration_ms: int | None,
) -> dict:
    return {
        "id": node.id,
        "key": node.node_key,
        "label": node.label,
        "kind": node.kind,
        "status": status,
        "parent_key": node.parent_key,
        "sort_order": node.sort_order,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": duration_ms,
        "meta": _parse_meta(node.meta),
    }


def _apply_container_rollups(
    nodes: list[ExecutionNode],
    task_status: str,
) -> list[dict]:
    sanitized: dict[str, ExecutionNode] = {}
    for node in nodes:
        sanitized_status = _sanitize_node_status(node, task_status)
        if sanitized_status == node.status:
            sanitized[node.node_key] = node
            continue
        clone = ExecutionNode(
            id=node.id,
            task_id=node.task_id,
            node_key=node.node_key,
            parent_key=node.parent_key,
            label=node.label,
            kind=node.kind,
            status=sanitized_status,
            started_at=node.started_at,
            ended_at=node.ended_at or (utcnow() if task_status == "completed" else None),
            duration_ms=node.duration_ms,
            meta=node.meta,
            sort_order=node.sort_order,
        )
        if clone.ended_at is not None and clone.started_at is not None:
            clone.duration_ms = _duration_between(clone.started_at, clone.ended_at)
        sanitized[node.node_key] = clone

    children_by_parent: dict[str, list[ExecutionNode]] = {}
    for node in sanitized.values():
        if node.parent_key:
            children_by_parent.setdefault(node.parent_key, []).append(node)

    node_out: list[dict] = []
    for node in sanitized.values():
        children = children_by_parent.get(node.node_key)
        if not children:
            duration_ms: int | None
            if node.status == "running" and node.started_at is not None:
                duration_ms = _live_duration_ms(node.started_at)
            else:
                duration_ms = node.duration_ms
            node_out.append(
                _build_node_dict(
                    node,
                    status=node.status,
                    started_at=node.started_at,
                    ended_at=node.ended_at,
                    duration_ms=duration_ms,
                )
            )
            continue

        child_statuses = [child.status for child in children]
        rolled_status = _rollup_container_status(child_statuses)
        started_at, ended_at, duration_ms = _rollup_container_times(
            children, rolled_status
        )
        node_out.append(
            _build_node_dict(
                node,
                status=rolled_status,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
            )
        )
    return node_out


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
    node_out = _apply_container_rollups(nodes, task.status)

    top_level_nodes = [n for n in node_out if not n["parent_key"]]
    status_counts = {
        "completed": 0,
        "running": 0,
        "failed": 0,
        "pending": 0,
        "interrupted": 0,
        "skipped": 0,
    }
    for node in top_level_nodes:
        status = node["status"]
        if status in status_counts:
            status_counts[status] += 1

    total_duration_ms = sum(n["duration_ms"] or 0 for n in top_level_nodes)

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
            "total_nodes": len(top_level_nodes),
            "completed": status_counts["completed"],
            "running": status_counts["running"],
            "failed": status_counts["failed"],
            "pending": status_counts["pending"],
            "total_duration_ms": total_duration_ms,
        },
        "nodes": node_out,
        "edges": edge_out,
    }
