from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from app.engine.base import BatchItemResult, RetrievedChunk

_COMPLIANCE_VALUES = (
    "satisfied",
    "violated",
    "cannot_satisfy",
    "insufficient_evidence",
)
_CONSEQUENCE_TAGS = (
    "no_score",
    "bid_unusable",
    "score_risk",
    "general_risk",
)


class MockBatchDiagnosisEngine:
    def __init__(self, delay_seconds: float = 0.5) -> None:
        self.delay_seconds = delay_seconds

    async def diagnose_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[BatchItemResult]:
        del category, retrieved_chunks
        await asyncio.sleep(self.delay_seconds)

        results: list[BatchItemResult] = []
        for item in items:
            item_id = item["id"]
            digest = int(hashlib.md5(str(item_id).encode("utf-8")).hexdigest(), 16)
            compliance = _COMPLIANCE_VALUES[digest % len(_COMPLIANCE_VALUES)]
            tag_count = (digest // len(_COMPLIANCE_VALUES)) % len(_CONSEQUENCE_TAGS) + 1
            consequence_tags: list[str] = []
            for offset in range(tag_count):
                tag = _CONSEQUENCE_TAGS[(digest + offset) % len(_CONSEQUENCE_TAGS)]
                if tag not in consequence_tags:
                    consequence_tags.append(tag)

            title = str(item.get("title", ""))
            requirement = str(item.get("requirement", ""))
            description = requirement[:120] if requirement else title

            results.append(
                BatchItemResult(
                    checklist_item_id=item_id,
                    compliance=compliance,
                    consequence_tags=consequence_tags,
                    evidence=(
                        f"[{task_id}] mock evidence for checklist item {item_id}"
                    ),
                    suggestion=(
                        f"Mock suggestion for 「{title}」: compliance={compliance}"
                    ),
                    response_content=(
                        f"[{task_id}] mock response content for checklist item {item_id}"
                    ),
                    description=description,
                )
            )
        return results
