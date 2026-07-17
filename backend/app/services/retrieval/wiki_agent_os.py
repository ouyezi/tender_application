"""Agent OS adapter for task-level wiki page generation.

Requires the Agent OS client from the ``2026-07-17-agent-os-tender-interpretation``
plan. Until that lands, keep ``AGENT_WIKI_BUILDER=mock``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


def _require_agent_os_client() -> Any:
    try:
        from app.services.agent_os import AgentOSClient
    except ImportError as exc:
        raise ImportError(
            "Agent OS client is unavailable. Set AGENT_WIKI_BUILDER=mock or "
            "install the agent-os interpretation client first."
        ) from exc
    return AgentOSClient


class AgentOSWikiBuilder:
    async def build_for_task(
        self,
        session: AsyncSession,
        task_id: str,
    ) -> None:
        del session
        client = _require_agent_os_client()()
        await client.invoke("wiki_builder", {"task_id": task_id})
