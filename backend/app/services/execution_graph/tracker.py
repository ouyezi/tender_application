from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import timezone
from typing import Any

from sqlalchemy import func, select

from app import db
from app.models import ExecutionEdge, ExecutionNode, utcnow
from app.services.execution_graph.template import TASK_GRAPH_EDGES, TASK_GRAPH_NODES

logger = logging.getLogger(__name__)


class ExecutionGraphTracker:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    async def init_graph(self) -> None:
        try:
            async with db.SessionLocal() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(ExecutionNode)
                    .where(ExecutionNode.task_id == self.task_id)
                )
                if count and count > 0:
                    return

                for node_def in TASK_GRAPH_NODES:
                    session.add(
                        ExecutionNode(
                            task_id=self.task_id,
                            node_key=node_def["node_key"],
                            parent_key=node_def.get("parent_key"),
                            label=node_def["label"],
                            kind=node_def["kind"],
                            sort_order=node_def.get("sort_order", 0),
                        )
                    )
                for edge_def in TASK_GRAPH_EDGES:
                    session.add(
                        ExecutionEdge(
                            task_id=self.task_id,
                            from_key=edge_def["from_key"],
                            to_key=edge_def["to_key"],
                            edge_kind=edge_def.get("edge_kind", "sequential"),
                        )
                    )
                await session.commit()
        except Exception as exc:
            logger.warning("init_graph failed for task %s: %s", self.task_id, exc)

    async def add_node(
        self,
        node_key: str,
        parent_key: str | None,
        label: str,
        kind: str,
        meta: dict[str, Any] | None = None,
        sort_order: int = 0,
    ) -> None:
        try:
            async with db.SessionLocal() as session:
                existing = await session.scalar(
                    select(ExecutionNode).where(
                        ExecutionNode.task_id == self.task_id,
                        ExecutionNode.node_key == node_key,
                    )
                )
                if existing is not None:
                    return

                session.add(
                    ExecutionNode(
                        task_id=self.task_id,
                        node_key=node_key,
                        parent_key=parent_key,
                        label=label,
                        kind=kind,
                        meta=json.dumps(meta or {}, ensure_ascii=False),
                        sort_order=sort_order,
                    )
                )
                if parent_key:
                    session.add(
                        ExecutionEdge(
                            task_id=self.task_id,
                            from_key=parent_key,
                            to_key=node_key,
                            edge_kind="sequential",
                        )
                    )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "add_node failed for task %s key %s: %s", self.task_id, node_key, exc
            )

    async def notify(
        self,
        node_key: str,
        status: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with db.SessionLocal() as session:
                node = await session.scalar(
                    select(ExecutionNode).where(
                        ExecutionNode.task_id == self.task_id,
                        ExecutionNode.node_key == node_key,
                    )
                )
                if node is None:
                    return
                if status is not None:
                    node.status = status
                if meta:
                    current = json.loads(node.meta or "{}")
                    current.update(meta)
                    node.meta = json.dumps(current, ensure_ascii=False)
                await session.commit()
        except Exception as exc:
            logger.warning(
                "notify failed for task %s key %s: %s", self.task_id, node_key, exc
            )

    async def _start_node(self, node_key: str) -> bool:
        try:
            async with db.SessionLocal() as session:
                node = await session.scalar(
                    select(ExecutionNode).where(
                        ExecutionNode.task_id == self.task_id,
                        ExecutionNode.node_key == node_key,
                    )
                )
                if node is None:
                    return False
                if node.status == "running":
                    logger.warning(
                        "node %s already running for task %s", node_key, self.task_id
                    )
                    return False
                node.status = "running"
                node.started_at = utcnow()
                await session.commit()
                return True
        except Exception as exc:
            logger.warning(
                "_start_node failed for task %s key %s: %s", self.task_id, node_key, exc
            )
            return False

    async def _finish_node(
        self,
        node_key: str,
        status: str,
        error: str | None = None,
    ) -> None:
        try:
            async with db.SessionLocal() as session:
                node = await session.scalar(
                    select(ExecutionNode).where(
                        ExecutionNode.task_id == self.task_id,
                        ExecutionNode.node_key == node_key,
                    )
                )
                if node is None:
                    return
                now = utcnow()
                node.status = status
                node.ended_at = now
                if node.started_at is not None:
                    started = node.started_at
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    delta = now - started
                    node.duration_ms = int(delta.total_seconds() * 1000)
                if error is not None:
                    current = json.loads(node.meta or "{}")
                    current["error"] = error
                    node.meta = json.dumps(current, ensure_ascii=False)
                await session.commit()
        except Exception as exc:
            logger.warning(
                "_finish_node failed for task %s key %s: %s", self.task_id, node_key, exc
            )

    async def _node_exists(self, node_key: str) -> bool:
        try:
            async with db.SessionLocal() as session:
                node = await session.scalar(
                    select(ExecutionNode).where(
                        ExecutionNode.task_id == self.task_id,
                        ExecutionNode.node_key == node_key,
                    )
                )
                return node is not None
        except Exception as exc:
            logger.warning(
                "_node_exists failed for task %s key %s: %s", self.task_id, node_key, exc
            )
            return False

    @asynccontextmanager
    async def track(
        self,
        node_key: str,
        label: str | None = None,
        kind: str | None = None,
    ):
        if not await self._node_exists(node_key):
            await self.add_node(
                node_key=node_key,
                parent_key=None,
                label=label or node_key,
                kind=kind or "stage",
            )
        await self._start_node(node_key)
        try:
            yield
            await self._finish_node(node_key, "completed")
        except Exception as exc:
            await self._finish_node(node_key, "failed", error=str(exc))
            raise

    def track_node(self, node_key: str, **kwargs):
        return self.track(node_key, **kwargs)


def get_tracker(task_id: str) -> ExecutionGraphTracker:
    return ExecutionGraphTracker(task_id)
