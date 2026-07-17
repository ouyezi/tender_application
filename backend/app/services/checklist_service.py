from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from typing import Any

from app import config, db
from app.engine.base import ChecklistAgent, ChecklistDraft
from app.models import (
    ChecklistCategory,
    ChecklistGeneration,
    ChecklistItem,
    DiagnosisTask,
    utcnow,
)
from app.services.artifact import write_checklist_json
from app.services.checklist_context import (
    PromptContext,
    build_prompt_context,
    load_task_source,
)

_IMPORTANCE_VALUES = {"high", "medium", "low"}
_COMPLIANCE_KEYS = {
    "satisfied",
    "violated",
    "cannot_satisfy",
    "insufficient_evidence",
}
_CONSEQUENCE_KEYS = {
    "no_score",
    "bid_unusable",
    "score_risk",
    "general_risk",
}


class ChecklistValidationError(RuntimeError):
    pass


def _require_nonempty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ChecklistValidationError(f"{field} must be a non-empty string")


def _require_string_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, list) or (
        not allow_empty and not value
    ) or any(not isinstance(entry, str) or not entry.strip() for entry in value):
        qualifier = "" if allow_empty else "non-empty "
        raise ChecklistValidationError(
            f"{field} must be a {qualifier}list[str]"
        )


def _require_rules(value: Any, field: str, allowed_keys: set[str]) -> None:
    if not isinstance(value, dict) or not value:
        raise ChecklistValidationError(f"{field} must be a non-empty dict[str, str]")
    if any(key not in allowed_keys for key in value):
        raise ChecklistValidationError(f"{field} contains an unsupported key")
    if any(
        not isinstance(key, str)
        or not isinstance(rule, str)
        or not rule.strip()
        for key, rule in value.items()
    ):
        raise ChecklistValidationError(f"{field} values must be non-empty strings")


def _validate_source_reference(
    reference: Any,
    context: PromptContext,
    tender_markdown: str,
    item_id: str,
) -> None:
    if not isinstance(reference, dict):
        raise ChecklistValidationError(
            f"item {item_id} source_references entries must be objects"
        )
    coordinate_space = reference.get("coordinate_space")
    start = reference.get("start")
    end = reference.get("end")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
    ):
        raise ChecklistValidationError(f"item {item_id} source offset must be integers")

    if coordinate_space == "segment":
        segment_index = reference.get("segment_index")
        if (
            not isinstance(segment_index, int)
            or isinstance(segment_index, bool)
            or segment_index < 0
            or segment_index >= len(context.segments)
        ):
            raise ChecklistValidationError(
                f"item {item_id} source segment_index is invalid"
            )
        segment = context.segments[segment_index]
        if not (0 <= start < end <= len(segment)) or not segment[start:end].strip():
            raise ChecklistValidationError(
                f"item {item_id} source offset is invalid"
            )
        return

    if coordinate_space == "synthetic":
        if not (
            tender_markdown == ""
            and reference.get("synthetic") is True
            and start == 0
            and end == 1
        ):
            raise ChecklistValidationError(
                f"item {item_id} synthetic source is invalid"
            )
        return

    raise ChecklistValidationError(
        f"item {item_id} coordinate_space is unsupported"
    )


def validate_draft(
    draft: ChecklistDraft,
    context: PromptContext,
    tender_markdown: str,
) -> None:
    if draft.schema_version != config.CHECKLIST_SCHEMA_VERSION:
        raise ChecklistValidationError("schema_version does not match configuration")
    if not isinstance(draft.categories, list) or not draft.categories:
        raise ChecklistValidationError("categories must be non-empty")
    if not isinstance(draft.items, list) or not draft.items:
        raise ChecklistValidationError("items must be non-empty")

    category_ids: set[str] = set()
    for category in draft.categories:
        _require_nonempty_string(category.id, "category id")
        if category.id in category_ids:
            raise ChecklistValidationError("category local id must be unique")
        category_ids.add(category.id)
        _require_nonempty_string(category.name, "category name")
        _require_nonempty_string(category.description, "category description")
        _require_nonempty_string(category.retrieval_query, "category retrieval_query")
        _require_string_list(
            category.expected_locations,
            "category expected_locations",
            allow_empty=True,
        )

    item_ids: set[str] = set()
    normalized_items: set[tuple[str, str]] = set()
    category_counts = {category_id: 0 for category_id in category_ids}
    for item in draft.items:
        _require_nonempty_string(item.id, "item id")
        if item.id in item_ids:
            raise ChecklistValidationError("item local id must be unique")
        item_ids.add(item.id)
        if item.category_id not in category_ids:
            raise ChecklistValidationError(
                f"item {item.id} references an unknown category"
            )
        category_counts[item.category_id] += 1
        if category_counts[item.category_id] > config.CHECKLIST_MAX_ITEMS_PER_CATEGORY:
            raise ChecklistValidationError(
                "category exceeds maximum items per category"
            )

        _require_nonempty_string(item.title, "item title")
        _require_nonempty_string(item.requirement, "item requirement")
        _require_nonempty_string(item.technique, "item technique")
        normalized = (
            re.sub(r"\s+", "", item.title).casefold(),
            re.sub(r"\s+", "", item.requirement).casefold(),
        )
        if normalized in normalized_items:
            raise ChecklistValidationError(
                "duplicate item title and requirement"
            )
        normalized_items.add(normalized)
        if item.importance not in _IMPORTANCE_VALUES:
            raise ChecklistValidationError(
                f"item {item.id} importance is invalid"
            )
        if not isinstance(item.source_references, list) or not item.source_references:
            raise ChecklistValidationError(
                f"item {item.id} source_references must be non-empty"
            )
        for reference in item.source_references:
            _validate_source_reference(reference, context, tender_markdown, item.id)
        _require_string_list(item.retrieval_hints, "item retrieval_hints")
        _require_string_list(item.expected_evidence, "item expected_evidence")
        if (
            not isinstance(item.admin_config_refs, list)
            or any(
                not isinstance(reference, int) or isinstance(reference, bool)
                for reference in item.admin_config_refs
            )
        ):
            raise ChecklistValidationError(
                "item admin_config_refs must be list[int]"
            )
        _require_rules(
            item.compliance_rules,
            "item compliance_rules",
            _COMPLIANCE_KEYS,
        )
        _require_rules(
            item.consequence_rules,
            "item consequence_rules",
            _CONSEQUENCE_KEYS,
        )

    try:
        json.dumps(draft.raw_response, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ChecklistValidationError(
            "raw_response must be JSON serializable"
        ) from exc


def _global_id(prefix: str, generation_id: int, local_id: str) -> str:
    digest = hashlib.sha1(local_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{generation_id}-{digest}"


def _build_published_payload(
    draft: ChecklistDraft,
    generation_id: int,
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    category_map = {
        category.id: _global_id("cat", generation_id, category.id)
        for category in draft.categories
    }
    item_map = {
        item.id: _global_id("item", generation_id, item.id)
        for item in draft.items
    }
    if len(set(category_map.values())) != len(category_map):
        raise ChecklistValidationError("category global ID hash collision")
    if len(set(item_map.values())) != len(item_map):
        raise ChecklistValidationError("item global ID hash collision")

    payload = asdict(draft)
    for category in payload["categories"]:
        category["id"] = category_map[category["id"]]
    for item in payload["items"]:
        item["id"] = item_map[item["id"]]
        item["category_id"] = category_map[item["category_id"]]
    return payload, category_map, item_map


class ChecklistService:
    def __init__(self, agent: ChecklistAgent):
        self.agent = agent

    async def generate_for_task(self, task_id: str) -> int:
        generation_id: int | None = None
        try:
            _, tender_markdown, interpret_markdown, admin_configs = (
                await load_task_source(task_id)
            )
            context = build_prompt_context(
                tender_markdown,
                interpret_markdown,
                admin_configs,
                config.CHECKLIST_SINGLE_PASS_TOKENS,
                config.CHECKLIST_CHUNK_TOKENS,
                config.CHECKLIST_CHUNK_OVERLAP_TOKENS,
            )
            input_hash = hashlib.sha256(
                (
                    context.stable_prefix
                    + "".join(context.segments)
                ).encode("utf-8")
            ).hexdigest()
            generation_id = await self._create_attempt(
                task_id,
                input_hash,
                admin_configs,
            )

            draft = await self.agent.generate(task_id=task_id, context=context)
            raw_path = write_checklist_json(
                task_id,
                f"checklist-generation-{generation_id}-raw.json",
                asdict(draft),
            )
            await self._save_raw_path(generation_id, raw_path.as_posix())

            validate_draft(draft, context, tender_markdown)
            payload, category_map, item_map = _build_published_payload(
                draft,
                generation_id,
            )
            write_checklist_json(
                task_id,
                f"checklist-generation-{generation_id}.json",
                payload,
            )
            await self._publish(
                task_id,
                generation_id,
                draft,
                category_map,
                item_map,
            )
            return generation_id
        except Exception as exc:
            await self._fail(task_id, generation_id, exc)
            raise

    async def _create_attempt(
        self,
        task_id: str,
        input_hash: str,
        admin_configs: list[Any],
    ) -> int:
        generation = ChecklistGeneration(
            task_id=task_id,
            status="generating",
            agent_type=str(
                getattr(self.agent, "agent_type", config.CHECKLIST_AGENT)
            ),
            agent_version=str(
                getattr(self.agent, "agent_version", config.CHECKLIST_AGENT_VERSION)
            ),
            schema_version=config.CHECKLIST_SCHEMA_VERSION,
            input_hash=input_hash,
            admin_config_snapshot=json.dumps(
                admin_configs,
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        async with db.SessionLocal() as session:
            session.add(generation)
            await session.flush()
            generation_id = generation.id
            await session.commit()
        return generation_id

    async def _save_raw_path(self, generation_id: int, path: str) -> None:
        async with db.SessionLocal() as session:
            async with session.begin():
                generation = await session.get(ChecklistGeneration, generation_id)
                if generation is None:
                    raise RuntimeError("checklist generation disappeared")
                generation.raw_response_path = path

    async def _publish(
        self,
        task_id: str,
        generation_id: int,
        draft: ChecklistDraft,
        category_map: dict[str, str],
        item_map: dict[str, str],
    ) -> None:
        async with db.SessionLocal() as session:
            async with session.begin():
                generation = await session.get(ChecklistGeneration, generation_id)
                task = await session.get(DiagnosisTask, task_id)
                if generation is None or task is None:
                    raise RuntimeError("publish target disappeared")

                session.add_all(
                    [
                        ChecklistCategory(
                            id=category_map[category.id],
                            generation_id=generation_id,
                            name=category.name,
                            description=category.description,
                            retrieval_query=category.retrieval_query,
                            expected_locations=json.dumps(
                                category.expected_locations,
                                ensure_ascii=False,
                            ),
                            sort_order=category.sort_order,
                        )
                        for category in draft.categories
                    ]
                )
                await session.flush()
                session.add_all(
                    [
                        ChecklistItem(
                            id=item_map[item.id],
                            generation_id=generation_id,
                            category_id=category_map[item.category_id],
                            title=item.title,
                            requirement=item.requirement,
                            technique=item.technique,
                            importance=item.importance,
                            source_references=json.dumps(
                                item.source_references,
                                ensure_ascii=False,
                            ),
                            retrieval_hints=json.dumps(
                                item.retrieval_hints,
                                ensure_ascii=False,
                            ),
                            expected_evidence=json.dumps(
                                item.expected_evidence,
                                ensure_ascii=False,
                            ),
                            compliance_rules=json.dumps(
                                item.compliance_rules,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            consequence_rules=json.dumps(
                                item.consequence_rules,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            admin_config_refs=json.dumps(item.admin_config_refs),
                            sort_order=item.sort_order,
                        )
                        for item in draft.items
                    ]
                )
                await session.flush()

                now = utcnow()
                generation.status = "succeeded"
                generation.error_message = None
                generation.finished_at = now
                task.current_checklist_generation_id = generation_id
                task.progress_done = 0
                task.progress_total = len(draft.items)
                task.error_message = None
                task.finished_at = None
                task.updated_at = now

    async def _fail(
        self,
        task_id: str,
        generation_id: int | None,
        error: Exception,
    ) -> None:
        message = f"{type(error).__name__}: {error}"
        now = utcnow()
        async with db.SessionLocal() as session:
            async with session.begin():
                if generation_id is not None:
                    generation = await session.get(
                        ChecklistGeneration,
                        generation_id,
                    )
                    if generation is not None:
                        generation.status = "failed"
                        generation.error_message = message
                        generation.finished_at = now
                task = await session.get(DiagnosisTask, task_id)
                if task is not None:
                    task.status = "failed"
                    task.error_message = message
                    task.finished_at = now
                    task.updated_at = now
