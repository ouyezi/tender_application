from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from app import config
from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.engine.checklist_merge import merge_checklist_drafts
from app.services.agent_os import AgentOSClient
from app.services.checklist_context import PromptContext

TENDER_CHECKLIST_GENERATOR_APP_NAME = "tender_checklist_generator_app"

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]


class ChecklistAgentResponseError(ValueError):
    pass


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ChecklistAgentResponseError(f"missing or empty {key}")
    return value


def parse_checklist_payload(payload: dict[str, Any]) -> ChecklistDraft:
    if not isinstance(payload, dict):
        raise ChecklistAgentResponseError("payload must be object")
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ChecklistAgentResponseError("schema_version invalid")
    categories_raw = _require_list(payload, "categories")
    items_raw = _require_list(payload, "items")
    categories: list[ChecklistCategoryDraft] = []
    for row in categories_raw:
        if not isinstance(row, dict):
            raise ChecklistAgentResponseError("category must be object")
        locations = row.get("expected_locations")
        if not isinstance(locations, list):
            raise ChecklistAgentResponseError("expected_locations must be list")
        categories.append(
            ChecklistCategoryDraft(
                id=str(row.get("id", "")),
                name=str(row.get("name", "")),
                description=str(row.get("description", "")),
                retrieval_query=str(row.get("retrieval_query", "")),
                expected_locations=[str(x) for x in locations],
                sort_order=int(row.get("sort_order", 0)),
            )
        )
    items: list[ChecklistItemDraft] = []
    for row in items_raw:
        if not isinstance(row, dict):
            raise ChecklistAgentResponseError("item must be object")
        source_references = row.get("source_references")
        retrieval_hints = row.get("retrieval_hints")
        expected_evidence = row.get("expected_evidence")
        compliance_rules = row.get("compliance_rules")
        consequence_rules = row.get("consequence_rules")
        admin_config_refs = row.get("admin_config_refs")
        if not isinstance(source_references, list):
            raise ChecklistAgentResponseError("source_references must be list")
        if not isinstance(retrieval_hints, list):
            raise ChecklistAgentResponseError("retrieval_hints must be list")
        if not isinstance(expected_evidence, list):
            raise ChecklistAgentResponseError("expected_evidence must be list")
        if not isinstance(compliance_rules, dict):
            raise ChecklistAgentResponseError("compliance_rules must be object")
        if not isinstance(consequence_rules, dict):
            raise ChecklistAgentResponseError("consequence_rules must be object")
        if not isinstance(admin_config_refs, list):
            raise ChecklistAgentResponseError("admin_config_refs must be list")
        items.append(
            ChecklistItemDraft(
                id=str(row.get("id", "")),
                category_id=str(row.get("category_id", "")),
                title=str(row.get("title", "")),
                requirement=str(row.get("requirement", "")),
                technique=str(row.get("technique", "")),
                importance=str(row.get("importance", "")),
                source_references=list(source_references),
                retrieval_hints=[str(x) for x in retrieval_hints],
                expected_evidence=[str(x) for x in expected_evidence],
                compliance_rules={str(k): str(v) for k, v in compliance_rules.items()},
                consequence_rules={str(k): str(v) for k, v in consequence_rules.items()},
                admin_config_refs=[int(x) for x in admin_config_refs],
                sort_order=int(row.get("sort_order", 0)),
            )
        )
    return ChecklistDraft(
        schema_version=schema_version,
        categories=categories,
        items=items,
        raw_response=payload,
    )


class AgentOSChecklistAgent:
    agent_type = "agent_os"
    agent_version = "1"

    def __init__(
        self,
        *,
        app_name: str = TENDER_CHECKLIST_GENERATOR_APP_NAME,
        client: Optional[AgentOSClient] = None,
        invoke_app: Optional[InvokeFn] = None,
    ) -> None:
        self.app_name = app_name
        self._client = client
        self._invoke_app = invoke_app

    async def _invoke(self, input_data: dict[str, object]) -> dict[str, object]:
        if self._invoke_app is not None:
            return await self._invoke_app(self.app_name, input_data)
        client = self._client or AgentOSClient()
        return await client.invoke_app(self.app_name, input_data)

    async def generate(
        self,
        *,
        task_id: str,
        context: PromptContext,
    ) -> ChecklistDraft:
        del task_id
        partials: list[ChecklistDraft] = []
        for call in context.calls:
            payload = await self._invoke(
                {
                    "system_instructions": call.system_instructions,
                    "interpret_report": call.interpret_report,
                    "admin_config": call.admin_config,
                    "tender_segment": call.tender_segment,
                }
            )
            partials.append(parse_checklist_payload(payload))
        return merge_checklist_drafts(
            partials,
            max_items_per_category=config.CHECKLIST_MAX_ITEMS_PER_CATEGORY,
        )
