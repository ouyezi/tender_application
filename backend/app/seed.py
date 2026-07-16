from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import DiagnosisConfig

SEED_CONFIGS: list[dict] = [
    {
        "title": "企业资质核验",
        "technique": "对照招标资格要求",
        "content_mode": "description",
        "content_scope": None,
        "content_text": "所有资质文件",
        "importance": "high",
    },
    {
        "title": "目录完整性",
        "technique": "检查目录与正文",
        "content_mode": "full_text",
        "content_scope": "directory",
        "content_text": None,
        "importance": "medium",
    },
    {
        "title": "偏差表响应",
        "technique": "逐条核对偏差表与招标要求",
        "content_mode": "description",
        "content_scope": None,
        "content_text": "偏差表章节",
        "importance": "high",
    },
]


async def seed_configs_if_empty() -> None:
    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(DiagnosisConfig))
        if count:
            return
        for data in SEED_CONFIGS:
            session.add(DiagnosisConfig(**data))
        await session.commit()
