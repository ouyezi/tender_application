from __future__ import annotations

import json
from typing import Any, Protocol

from app.engine.base import BatchItemResult, RetrievedChunk
from app.services.batch_diagnosis_context import SYSTEM_INSTRUCTIONS

TENDER_BATCH_DIAGNOSIS_APP_NAME = "tender_batch_diagnosis_app"

_COMPLIANCE = frozenset(
    {"satisfied", "violated", "cannot_satisfy", "insufficient_evidence"}
)
_TAGS = frozenset({"no_score", "bid_unusable", "score_risk", "general_risk"})


class AgentOSInvoker(Protocol):
    async def invoke_app(
        self, app_name: str, input_data: dict[str, object]
    ) -> dict[str, Any]: ...


class BatchDiagnosisResponseError(ValueError):
    pass


def parse_batch_diagnosis_payload(payload: dict[str, Any]) -> list[BatchItemResult]:
    if not isinstance(payload, dict):
        raise BatchDiagnosisResponseError("payload must be object")
    if payload.get("schema_version") != "1":
        raise BatchDiagnosisResponseError("schema_version invalid")
    results_raw = payload.get("results")
    if not isinstance(results_raw, list) or not results_raw:
        raise BatchDiagnosisResponseError("missing or empty results")
    out: list[BatchItemResult] = []
    for row in results_raw:
        if not isinstance(row, dict):
            raise BatchDiagnosisResponseError("result row must be object")
        item_id = str(row.get("checklist_item_id") or "").strip()
        compliance = str(row.get("compliance") or "").strip()
        if not item_id:
            raise BatchDiagnosisResponseError("checklist_item_id missing")
        if compliance not in _COMPLIANCE:
            raise BatchDiagnosisResponseError("compliance invalid")
        tags_raw = row.get("consequence_tags") or []
        if not isinstance(tags_raw, list):
            raise BatchDiagnosisResponseError("consequence_tags must be list")
        tags: list[str] = []
        for tag in tags_raw:
            t = str(tag).strip()
            if t not in _TAGS:
                raise BatchDiagnosisResponseError("consequence_tags invalid")
            if t not in tags:
                tags.append(t)
        out.append(
            BatchItemResult(
                checklist_item_id=item_id,
                compliance=compliance,
                consequence_tags=tags,
                evidence=str(row.get("evidence") or ""),
                suggestion=str(row.get("suggestion") or ""),
                description=str(row.get("description") or ""),
            )
        )
    return out


class AgentOSBatchDiagnosisEngine:
    def __init__(
        self,
        client: AgentOSInvoker,
        *,
        app_name: str = TENDER_BATCH_DIAGNOSIS_APP_NAME,
    ) -> None:
        self._client = client
        self._app_name = app_name

    async def diagnose_category(
        self,
        *,
        task_id: str,
        category: dict[str, Any],
        items: list[dict[str, Any]],
        retrieved_chunks: list[RetrievedChunk],
    ) -> list[BatchItemResult]:
        del task_id
        category_payload = {
            "category": {
                "id": category.get("id"),
                "name": category.get("name"),
                "description": category.get("description", ""),
            },
            "items": items,
        }
        chunks_payload = [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "location": c.location,
                "document_role": c.document_role,
            }
            for c in retrieved_chunks
        ]
        response = await self._client.invoke_app(
            self._app_name,
            {
                "system_instructions": SYSTEM_INSTRUCTIONS,
                "category_payload": json.dumps(
                    category_payload, ensure_ascii=False
                ),
                "retrieved_chunks": json.dumps(
                    chunks_payload, ensure_ascii=False
                ),
            },
        )
        return parse_batch_diagnosis_payload(response)
