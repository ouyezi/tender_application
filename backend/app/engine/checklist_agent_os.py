from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Mapping, Optional

from app import config
from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.engine.checklist_merge import merge_checklist_drafts
from app.services.agent_os import AgentOSClient
from app.services.checklist_categories import (
    FIXED_CATEGORY_IDS,
    fixed_categories_draft,
)
from app.services.checklist_context import PromptContext

TENDER_CHECKLIST_GENERATOR_APP_NAME = "tender_checklist_generator_app"

logger = logging.getLogger(__name__)

InvokeFn = Callable[[str, dict[str, object]], Awaitable[dict[str, object]]]

_DIAGNOSIS_MODE_VALUES = frozenset({"file", "offline"})


def _normalize_diagnosis_mode(value: Any) -> str:
    if isinstance(value, str) and value.strip() in _DIAGNOSIS_MODE_VALUES:
        return value.strip()
    return "file"


class ChecklistAgentResponseError(ValueError):
    pass


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ChecklistAgentResponseError(f"missing or empty {key}")
    return value


def _as_string_list(value: Any, *, fallback: list[str] | None = None) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if fallback is not None:
        return list(fallback)
    raise ChecklistAgentResponseError("expected list")


def _as_nonempty_str(value: Any, fallback: str = "") -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return fallback


def _as_sort_order(value: Any, index: int) -> int:
    try:
        order = int(value)
    except (TypeError, ValueError):
        return index + 1
    if isinstance(value, bool) or order < 0:
        return index + 1
    return order


def _normalize_consequence_rules(value: Any) -> dict[str, str]:
    if isinstance(value, dict) and value:
        normalized: dict[str, str] = {}
        for key, rule in value.items():
            key_str = str(key).strip()
            if not key_str:
                continue
            if isinstance(rule, bool):
                normalized[key_str] = key_str if rule else f"not_{key_str}"
            else:
                text = str(rule).strip()
                if text:
                    normalized[key_str] = text
        if normalized:
            return normalized
    if isinstance(value, list) and value:
        normalized = {
            str(key).strip(): str(key).strip()
            for key in value
            if str(key).strip()
        }
        if normalized:
            return normalized
    raise ChecklistAgentResponseError("consequence_rules must be object")


def _normalize_admin_config_refs(value: Any) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ChecklistAgentResponseError("admin_config_refs must be list")
    refs: list[int] = []
    for entry in value:
        try:
            number = int(entry)
        except (TypeError, ValueError) as exc:
            raise ChecklistAgentResponseError(
                "admin_config_refs must be list"
            ) from exc
        if isinstance(entry, bool) or number < 0:
            raise ChecklistAgentResponseError("admin_config_refs must be list")
        refs.append(number)
    return refs


def _require_markdown(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChecklistAgentResponseError(f"missing or empty {field}")
    return value.strip()


def _parse_v2_item(row: dict[str, Any], index: int) -> ChecklistItemDraft:
    title = _as_nonempty_str(row.get("title"))
    if not title:
        raise ChecklistAgentResponseError("item title required")
    category_id = str(row.get("category_id", "")).strip()
    if category_id not in FIXED_CATEGORY_IDS:
        raise ChecklistAgentResponseError("invalid category_id")
    retrieval_hints = _as_string_list(row.get("retrieval_hints"), fallback=[title])
    if not retrieval_hints:
        retrieval_hints = [title]
    admin_config_refs = _normalize_admin_config_refs(row.get("admin_config_refs"))
    content_target = row.get("content_target")
    if not isinstance(content_target, dict):
        content_target = {}
    else:
        content_target = dict(content_target)
    if not str(content_target.get("query") or "").strip():
        content_target["query"] = title
    if not str(content_target.get("file_role") or "").strip():
        content_target["file_role"] = "bid"
    return ChecklistItemDraft(
        id=_as_nonempty_str(row.get("id"), f"item-{index + 1}"),
        category_id=category_id,
        title=title,
        requirement=_as_nonempty_str(row.get("requirement")),
        technique=_as_nonempty_str(row.get("technique"), "对照招标文件"),
        importance=_as_nonempty_str(row.get("importance"), "medium"),
        source_citations=_require_markdown(row.get("source_citations"), "source_citations"),
        retrieval_hints=retrieval_hints,
        expected_evidence=_require_markdown(
            row.get("expected_evidence"), "expected_evidence"
        ),
        compliance_rules=_require_markdown(row.get("compliance_rules"), "compliance_rules"),
        consequence_rules=_require_markdown(
            row.get("consequence_rules"), "consequence_rules"
        ),
        admin_config_refs=admin_config_refs,
        sort_order=_as_sort_order(row.get("sort_order"), index),
        content_target=content_target,
        diagnosis_mode=_normalize_diagnosis_mode(row.get("diagnosis_mode")),
    )


def _parse_v2_payload(payload: dict[str, Any]) -> ChecklistDraft:
    items_raw = _require_list(payload, "items")
    items = [_parse_v2_item(row, index) for index, row in enumerate(items_raw)]
    return ChecklistDraft(
        schema_version="2",
        categories=fixed_categories_draft(),
        items=items,
        raw_response=payload,
    )


def _v1_source_to_citations(source_references: list[Any]) -> str:
    lines: list[str] = []
    for ref in source_references:
        if not isinstance(ref, dict):
            continue
        section = str(ref.get("section") or "").strip()
        if section:
            lines.append(f"- 章节：{section}")
    return "\n".join(lines) if lines else "- 章节：未标注"


def _v1_list_to_markdown(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values if value.strip())


def _v1_compliance_to_markdown(rules: dict[str, str]) -> str:
    labels = {
        "satisfied": "满足",
        "violated": "违反",
        "cannot_satisfy": "不能满足",
        "insufficient_evidence": "证据不足",
    }
    parts: list[str] = []
    for key, label in labels.items():
        text = str(rules.get(key) or "").strip() or "无"
        parts.append(f"## {label}\n{text}")
    return "\n\n".join(parts)


def _v1_consequence_to_markdown(rules: dict[str, str]) -> str:
    if not rules:
        return "[general_risk]\n存在合规风险"
    tag, text = next(iter(rules.items()))
    return f"[{tag}]\n{text}"


def parse_checklist_payload(payload: dict[str, Any]) -> ChecklistDraft:
    if not isinstance(payload, dict):
        raise ChecklistAgentResponseError("payload must be object")
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ChecklistAgentResponseError("schema_version invalid")
    if schema_version.strip() == "2":
        return _parse_v2_payload(payload)
    return _parse_v1_payload(payload, schema_version.strip())


def _parse_v1_payload(payload: dict[str, Any], schema_version: str) -> ChecklistDraft:
    categories_raw = _require_list(payload, "categories")
    items_raw = _require_list(payload, "items")
    categories: list[ChecklistCategoryDraft] = []
    for index, row in enumerate(categories_raw):
        if not isinstance(row, dict):
            raise ChecklistAgentResponseError("category must be object")
        name = _as_nonempty_str(row.get("name"))
        if not name:
            raise ChecklistAgentResponseError("category name required")
        category_id = _as_nonempty_str(row.get("id"), f"category-{index + 1}")
        description = _as_nonempty_str(row.get("description"), name)
        retrieval_query = _as_nonempty_str(row.get("retrieval_query"), name)
        locations = row.get("expected_locations")
        if locations is None:
            locations = []
        if not isinstance(locations, list):
            raise ChecklistAgentResponseError("expected_locations must be list")
        categories.append(
            ChecklistCategoryDraft(
                id=category_id,
                name=name,
                description=description,
                retrieval_query=retrieval_query,
                expected_locations=[str(x) for x in locations if str(x).strip()],
                sort_order=_as_sort_order(row.get("sort_order"), index),
            )
        )
    items: list[ChecklistItemDraft] = []
    for index, row in enumerate(items_raw):
        if not isinstance(row, dict):
            raise ChecklistAgentResponseError("item must be object")
        title = _as_nonempty_str(row.get("title"))
        if not title:
            raise ChecklistAgentResponseError("item title required")
        source_references = row.get("source_references")
        if not isinstance(source_references, list):
            raise ChecklistAgentResponseError("source_references must be list")
        compliance_rules = row.get("compliance_rules")
        if not isinstance(compliance_rules, dict):
            raise ChecklistAgentResponseError("compliance_rules must be object")
        consequence_rules = _normalize_consequence_rules(row.get("consequence_rules"))
        retrieval_hints = _as_string_list(
            row.get("retrieval_hints"),
            fallback=[title],
        )
        expected_evidence = _as_string_list(
            row.get("expected_evidence"),
            fallback=[title],
        )
        if not retrieval_hints:
            retrieval_hints = [title]
        if not expected_evidence:
            expected_evidence = [title]
        admin_config_refs = _normalize_admin_config_refs(row.get("admin_config_refs"))
        content_target = row.get("content_target")
        if not isinstance(content_target, dict):
            content_target = {}
        else:
            content_target = dict(content_target)
        if not str(content_target.get("query") or "").strip():
            content_target["query"] = title
        if not str(content_target.get("file_role") or "").strip():
            content_target["file_role"] = "bid"
        items.append(
            ChecklistItemDraft(
                id=_as_nonempty_str(row.get("id"), f"item-{index + 1}"),
                category_id=str(row.get("category_id", "")),
                title=title,
                requirement=_as_nonempty_str(row.get("requirement")),
                technique=_as_nonempty_str(row.get("technique"), "对照招标文件"),
                importance=_as_nonempty_str(row.get("importance"), "medium"),
                source_citations=_v1_source_to_citations(source_references),
                retrieval_hints=retrieval_hints,
                expected_evidence=_v1_list_to_markdown(expected_evidence),
                compliance_rules=_v1_compliance_to_markdown(
                    {str(k): str(v) for k, v in compliance_rules.items()}
                ),
                consequence_rules=_v1_consequence_to_markdown(consequence_rules),
                admin_config_refs=admin_config_refs,
                sort_order=_as_sort_order(row.get("sort_order"), index),
                content_target=content_target,
                diagnosis_mode=_normalize_diagnosis_mode(row.get("diagnosis_mode")),
            )
        )
    known_category_ids = {category.id for category in categories}
    fallback_category_id = categories[0].id
    remapped_items: list[ChecklistItemDraft] = []
    for item in items:
        category_id = item.category_id.strip()
        if category_id not in known_category_ids:
            category_id = fallback_category_id
        if category_id != item.category_id:
            remapped_items.append(
                ChecklistItemDraft(
                    id=item.id,
                    category_id=category_id,
                    title=item.title,
                    requirement=item.requirement,
                    technique=item.technique,
                    importance=item.importance,
                    source_citations=item.source_citations,
                    retrieval_hints=item.retrieval_hints,
                    expected_evidence=item.expected_evidence,
                    compliance_rules=item.compliance_rules,
                    consequence_rules=item.consequence_rules,
                    admin_config_refs=item.admin_config_refs,
                    sort_order=item.sort_order,
                    content_source=item.content_source,
                    content_target=item.content_target,
                    diagnosis_mode=item.diagnosis_mode,
                )
            )
        else:
            remapped_items.append(item)
    return ChecklistDraft(
        schema_version=schema_version,
        categories=categories,
        items=remapped_items,
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

    async def _invoke(
        self,
        input_data: dict[str, object],
        *,
        log_context: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if self._invoke_app is not None:
            return await self._invoke_app(self.app_name, input_data)
        client = self._client or AgentOSClient()
        return await client.invoke_app(
            self.app_name,
            input_data,
            log_context=log_context,
        )

    async def generate(
        self,
        *,
        task_id: str,
        context: PromptContext,
    ) -> ChecklistDraft:
        segment_count = len(context.calls)
        logger.info(
            "Checklist generation starting task_id=%s segment_count=%d app=%s",
            task_id,
            segment_count,
            self.app_name,
        )
        partials: list[ChecklistDraft] = []
        for call in context.calls:
            segment_index = call.segment_index
            logger.info(
                "Checklist segment invoke starting task_id=%s segment_index=%d/%d segment_chars=%d",
                task_id,
                segment_index,
                segment_count,
                len(call.tender_segment),
            )
            started_at = time.monotonic()
            payload = await self._invoke(
                {
                    "system_instructions": call.system_instructions,
                    "interpret_report": call.interpret_report,
                    "admin_config": call.admin_config,
                    "tender_segment": call.tender_segment,
                },
                log_context={
                    "task_id": task_id,
                    "segment_index": segment_index,
                    "segment_count": segment_count,
                    "segment_chars": len(call.tender_segment),
                },
            )
            logger.info(
                "Checklist segment invoke finished task_id=%s segment_index=%d/%d elapsed_s=%.2f",
                task_id,
                segment_index,
                segment_count,
                time.monotonic() - started_at,
            )
            partials.append(parse_checklist_payload(payload))
        logger.info(
            "Checklist generation agent calls finished task_id=%s segment_count=%d",
            task_id,
            segment_count,
        )
        return merge_checklist_drafts(partials)
