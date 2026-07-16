from __future__ import annotations

import asyncio
from typing import Any

from app.engine.base import DiagnosisItemResult

RESULTS = ("通过", "风险", "缺失")


class MockEngine:
    def __init__(self, delay_seconds: float = 0.5) -> None:
        self.delay_seconds = delay_seconds

    async def diagnose_item(
        self,
        task_id: str,
        config_item: dict[str, Any],
        documents: dict[str, str],
    ) -> DiagnosisItemResult:
        await asyncio.sleep(self.delay_seconds)

        title = config_item.get("title", "")
        technique = config_item.get("technique", "")
        content_text = config_item.get("content_text", "")

        result = RESULTS[hash(title) % len(RESULTS)]

        if technique and content_text:
            description = f"{technique}：{content_text}"
        else:
            description = technique or content_text or title

        evidence = (
            f"[{task_id}] 依据「{title}」检查项，采用{technique}，"
            f"涉及内容：{content_text}。"
        )
        suggestion = (
            f"针对「{title}」的诊断结论为「{result}」，建议进一步核实相关材料。"
        )

        return DiagnosisItemResult(
            content_title=title,
            description=description,
            result=result,
            evidence=evidence,
            suggestion=suggestion,
            config_id=config_item.get("id"),
        )
