from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeTag


def validate_target_tags(target_tags: list[str], allowed: set[str]) -> tuple[bool, str]:
    bad = [t for t in target_tags if t not in allowed]
    if bad:
        return False, f"非法标签: {bad}; 合法: {sorted(allowed)}"
    return True, ""


def map_to_controlled_tags(
    raw_labels: list[str],
    *,
    catalog: list[dict],
    default_confidence: float = 0.8,
) -> list[dict]:
    alias_map: dict[str, str] = {}
    for row in catalog:
        alias_map[row["name"]] = row["name"]
        for a in row.get("aliases") or []:
            alias_map[a] = row["name"]
    out = []
    seen = set()
    for label in raw_labels:
        name = alias_map.get(label) or alias_map.get(label.strip())
        if name and name not in seen:
            seen.add(name)
            out.append({"name": name, "confidence": default_confidence})
    return out


async def load_tag_catalog(session: AsyncSession) -> list[dict]:
    """Load enabled knowledge tags as enricher catalog rows."""
    result = await session.execute(
        select(KnowledgeTag).where(KnowledgeTag.enabled == 1)
    )
    catalog = []
    for row in result.scalars().all():
        catalog.append(
            {
                "name": row.name,
                "aliases": json.loads(row.aliases or "[]"),
            }
        )
    return catalog
