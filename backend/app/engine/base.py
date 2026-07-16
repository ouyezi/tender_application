from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class DiagnosisItemResult:
    content_title: str
    description: str
    result: str
    evidence: str
    suggestion: str
    config_id: int | None = None


class DiagnosisEngine(Protocol):
    async def diagnose_item(
        self,
        task_id: str,
        config_item: dict[str, Any],
        documents: dict[str, str],
    ) -> DiagnosisItemResult: ...
