from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator

from sqlalchemy import delete, select, update

from app import config, db
from app.engine.base import (
    BatchItemResult,
    ChecklistAgent,
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.models import (
    ChecklistCategory,
    ChecklistGeneration,
    ChecklistItem,
    DiagnosisTask,
    WorkspaceFile,
    utcnow,
)
from app.services.artifact import (
    checklist_json_path,
    promote_staged_checklist_json,
    remove_staged_checklist,
    serialize_checklist_json,
    stage_checklist_json,
    staged_checklist_path,
    write_checklist_debug_json,
)
from app.services.checklist_context import (
    ChecklistInputError,
    PromptContext,
    build_prompt_context,
    load_task_source,
)

_IMPORTANCE_VALUES = {"high", "medium", "low"}
_COMPLIANCE_OPTIONAL_EMPTY_KEYS = frozenset({"cannot_satisfy", "insufficient_evidence"})
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
_CONTENT_SOURCE_VALUES = {
    "full_document",
    "collection",
    "large_segments",
    "precise_search",
}
_DIAGNOSIS_MODE_VALUES = frozenset({"file", "offline"})
_MAX_PUBLIC_ERROR_LENGTH = 240
logger = logging.getLogger(__name__)


def _normalize_diagnosis_mode(value: Any) -> str:
    if isinstance(value, str) and value.strip() in _DIAGNOSIS_MODE_VALUES:
        return value.strip()
    return "file"


@dataclass
class _TaskLockEntry:
    lock: asyncio.Lock
    users: int = 0


_TASK_LOCKS: dict[str, _TaskLockEntry] = {}


@asynccontextmanager
async def _task_generation_lock(task_id: str) -> AsyncIterator[None]:
    entry = _TASK_LOCKS.get(task_id)
    if entry is None:
        entry = _TaskLockEntry(lock=asyncio.Lock())
        _TASK_LOCKS[task_id] = entry
    entry.users += 1
    acquired = False
    try:
        await entry.lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            entry.lock.release()
        entry.users -= 1
        if entry.users == 0 and _TASK_LOCKS.get(task_id) is entry:
            del _TASK_LOCKS[task_id]


async def _wait_for_cleanup_task(cleanup_task: asyncio.Task[None]) -> None:
    current_task = asyncio.current_task()

    def consume_pending_cancellations() -> None:
        if current_task is None:
            return
        while current_task.cancelling():
            current_task.uncancel()

    consume_pending_cancellations()
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            consume_pending_cancellations()
    cleanup_task.result()


class ChecklistValidationError(RuntimeError):
    pass


class ChecklistTaskNotFound(LookupError):
    pass


class ChecklistNotAvailable(LookupError):
    pass


class TenderParseBlockedError(ChecklistInputError):
    """Raised when tender parse failed, is partial, or timed out while waiting."""


def failure_stage_for_error(
    error: Exception | None,
    *,
    public_message: str | None = None,
) -> str:
    if isinstance(error, ChecklistValidationError):
        return "checklist_validation"
    if isinstance(error, ChecklistInputError):
        message = str(error)
        if message.startswith("tender_parse_") or message in {
            "tender_parse_missing",
            "tender_file_task_mismatch",
        }:
            return "tender_parse"
    if public_message == "checklist_validation_failed":
        return "checklist_validation"
    return "checklist_generation"


async def wait_for_tender_parse_ready(
    task_id: str,
    timeout: float = 300.0,
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        async with db.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if task is None:
                raise ChecklistInputError("task_missing")
            if not task.tender_file_id:
                raise ChecklistInputError("tender_parse_missing")
            workspace_file = await session.get(WorkspaceFile, task.tender_file_id)
            if workspace_file is None or workspace_file.task_id != task_id:
                raise ChecklistInputError("tender_parse_missing")
            parse_status = workspace_file.parse_status or "missing"
            if parse_status == "succeeded":
                return
            if parse_status in ("failed", "partial"):
                raise TenderParseBlockedError(f"tender_parse_{parse_status}")
        if loop.time() >= deadline:
            raise TenderParseBlockedError("tender_parse_timeout")
        await asyncio.sleep(config.CHECKLIST_PARSE_POLL_SECONDS)


def assert_batch_complete(
    items: list[dict[str, Any]],
    results: list[BatchItemResult],
) -> None:
    expected_ids = {item["id"] for item in items}
    result_ids = [result.checklist_item_id for result in results]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("batch diagnosis result mapping contains duplicate item ids")
    if set(result_ids) != expected_ids:
        raise ValueError("batch diagnosis result mapping does not match items")


def _load_json_list(raw: str, *, entries: type | None = None) -> list[Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChecklistValidationError("stored checklist JSON is invalid") from exc
    if not isinstance(value, list) or (
        entries is not None
        and any(
            not isinstance(entry, entries)
            or (entries is int and isinstance(entry, bool))
            for entry in value
        )
    ):
        raise ChecklistValidationError("stored checklist JSON is invalid")
    return value


def _load_json_rules(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChecklistValidationError("stored checklist JSON is invalid") from exc
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(rule, str)
        for key, rule in value.items()
    ):
        raise ChecklistValidationError("stored checklist JSON is invalid")
    return value


def _load_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ChecklistValidationError("stored checklist JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ChecklistValidationError("stored checklist JSON is invalid")
    return value


def _try_load_json_list(raw: str) -> list[Any] | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


def _try_load_json_rules(raw: str) -> dict[str, str] | None:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if any(not isinstance(key, str) or not isinstance(rule, str) for key, rule in value.items()):
        return None
    return value


def _legacy_list_to_markdown(values: list[Any]) -> str:
    return "\n".join(f"- {str(value).strip()}" for value in values if str(value).strip())


def _legacy_compliance_to_markdown(rules: dict[str, str]) -> str:
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


def _legacy_consequence_to_markdown(rules: dict[str, str]) -> str:
    if not rules:
        return "[general_risk]\n存在合规风险"
    tag, text = next(iter(rules.items()))
    return f"[{tag}]\n{text}"


def _legacy_source_to_citations(source_references: list[Any]) -> str:
    lines: list[str] = []
    for reference in source_references:
        if not isinstance(reference, dict):
            continue
        section = str(reference.get("section") or "").strip()
        if section:
            lines.append(f"- 章节：{section}")
    return "\n".join(lines) if lines else "- 章节：未标注"


def _format_item_for_api(item: ChecklistItem, schema_version: str) -> dict[str, Any]:
    base = {
        "id": item.id,
        "title": item.title,
        "requirement": item.requirement,
        "technique": item.technique,
        "importance": item.importance,
        "retrieval_hints": _load_json_list(item.retrieval_hints, entries=str),
        "admin_config_refs": _load_json_list(item.admin_config_refs, entries=int),
        "content_source": item.content_source,
        "content_target": _load_json_object(item.content_target),
        "diagnosis_mode": _normalize_diagnosis_mode(item.diagnosis_mode),
        "sort_order": item.sort_order,
    }
    if schema_version == "2":
        return {
            **base,
            "source_citations": item.source_references or "",
            "expected_evidence": item.expected_evidence or "",
            "compliance_rules": item.compliance_rules or "",
            "consequence_rules": item.consequence_rules or "",
        }

    source_refs = _try_load_json_list(item.source_references or "[]")
    expected = _try_load_json_list(item.expected_evidence or "[]")
    compliance = _try_load_json_rules(item.compliance_rules or "{}")
    consequence = _try_load_json_rules(item.consequence_rules or "{}")
    return {
        **base,
        "source_citations": (
            _legacy_source_to_citations(source_refs)
            if source_refs is not None
            else (item.source_references or "")
        ),
        "expected_evidence": (
            _legacy_list_to_markdown(expected)
            if expected is not None
            else (item.expected_evidence or "")
        ),
        "compliance_rules": (
            _legacy_compliance_to_markdown(compliance)
            if compliance is not None
            else (item.compliance_rules or "")
        ),
        "consequence_rules": (
            _legacy_consequence_to_markdown(consequence)
            if consequence is not None
            else (item.consequence_rules or "")
        ),
    }


def _validate_content_target(content_source: str, content_target: dict[str, Any], item_id: str) -> None:
    if not isinstance(content_target, dict):
        raise ChecklistValidationError(f"item {item_id} content_target must be a dict")
    if content_source == "collection":
        tags = content_target.get("target_tags")
        if not isinstance(tags, list) or not tags or not all(isinstance(t, str) for t in tags):
            raise ChecklistValidationError(
                f"item {item_id} collection requires non-empty target_tags"
            )
    elif content_source == "precise_search":
        query = content_target.get("query")
        if query is not None and not isinstance(query, str):
            raise ChecklistValidationError(f"item {item_id} precise_search query must be str")
    elif content_source in {"full_document", "large_segments"}:
        file_role = content_target.get("file_role")
        if file_role is not None and file_role not in {"tender", "bid"}:
            raise ChecklistValidationError(
                f"item {item_id} file_role must be tender or bid"
            )


async def get_report(task_id: str) -> dict[str, Any]:
    async with db.SessionLocal() as session:
        task = await session.get(DiagnosisTask, task_id)
        if task is None:
            raise ChecklistTaskNotFound(task_id)
        if task.current_checklist_generation_id is None:
            raise ChecklistNotAvailable(task_id)

        generation = await session.get(
            ChecklistGeneration,
            task.current_checklist_generation_id,
        )
        if (
            generation is None
            or generation.task_id != task_id
            or generation.status != "succeeded"
        ):
            raise ChecklistNotAvailable(task_id)
        categories = list(
            (
                await session.scalars(
                    select(ChecklistCategory)
                    .where(ChecklistCategory.generation_id == generation.id)
                    .order_by(ChecklistCategory.sort_order, ChecklistCategory.id)
                )
            ).all()
        )
        items = list(
            (
                await session.scalars(
                    select(ChecklistItem)
                    .where(ChecklistItem.generation_id == generation.id)
                    .order_by(ChecklistItem.sort_order, ChecklistItem.id)
                )
            ).all()
        )

    item_groups: dict[str, list[dict[str, Any]]] = {
        category.id: [] for category in categories
    }
    importance_counts = {"high": 0, "medium": 0, "low": 0}
    for item in items:
        if item.category_id not in item_groups or item.importance not in importance_counts:
            raise ChecklistValidationError("stored checklist data is invalid")
        item_groups[item.category_id].append(
            _format_item_for_api(item, generation.schema_version)
        )
        importance_counts[item.importance] += 1

    category_payloads = [
        {
            "id": category.id,
            "name": category.name,
            "description": category.description,
            "retrieval_query": category.retrieval_query,
            "expected_locations": _load_json_list(
                category.expected_locations,
                entries=str,
            ),
            "sort_order": category.sort_order,
            "items": item_groups[category.id],
        }
        for category in categories
    ]
    return {
        "generation": {
            "id": generation.id,
            "status": generation.status,
            "agent_type": generation.agent_type,
            "agent_version": generation.agent_version,
            "schema_version": generation.schema_version,
            "error_message": generation.error_message,
            "created_at": generation.created_at,
            "finished_at": generation.finished_at,
        },
        "summary": {
            "category_count": len(categories),
            "item_count": len(items),
            "importance_counts": importance_counts,
        },
        "categories": category_payloads,
    }


def _require_nonempty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ChecklistValidationError(f"{field} must be a non-empty string")


def _require_nonnegative_int(value: Any, field: str) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ChecklistValidationError(
            f"{field} must be a non-negative integer"
        )


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


def _require_rules(
    value: Any,
    field: str,
    allowed_keys: set[str],
    *,
    optional_empty_keys: frozenset[str] | None = None,
) -> None:
    if not isinstance(value, dict) or not value:
        raise ChecklistValidationError(f"{field} must be a non-empty dict[str, str]")
    optional_empty = optional_empty_keys or frozenset()
    if any(
        not isinstance(key, str)
        or not isinstance(rule, str)
        or (not rule.strip() and key not in optional_empty)
        for key, rule in value.items()
    ):
        raise ChecklistValidationError(
            f"{field} must contain string keys and non-empty string values"
        )
    if any(key not in allowed_keys for key in value):
        raise ChecklistValidationError(f"{field} contains an unsupported key")


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
    if not isinstance(coordinate_space, str):
        raise ChecklistValidationError(
            f"item {item_id} source coordinate_space must be a string"
        )
    if "synthetic" in reference and not isinstance(reference["synthetic"], bool):
        raise ChecklistValidationError(
            f"item {item_id} source synthetic must be a boolean"
        )

    start = reference.get("start")
    end = reference.get("end")
    segment_index = reference.get("segment_index")
    _require_nonnegative_int(start, f"item {item_id} source start")
    _require_nonnegative_int(end, f"item {item_id} source end")
    _require_nonnegative_int(
        segment_index,
        f"item {item_id} source segment_index",
    )

    if coordinate_space == "segment":
        if segment_index >= len(context.segments):
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
    admin_configs: list[Any],
) -> None:
    if not isinstance(draft, ChecklistDraft):
        raise ChecklistValidationError("draft must be a ChecklistDraft")
    _require_nonempty_string(draft.schema_version, "schema_version")
    if draft.schema_version != config.CHECKLIST_SCHEMA_VERSION:
        raise ChecklistValidationError("schema_version does not match configuration")
    if not isinstance(draft.categories, list) or not draft.categories:
        raise ChecklistValidationError("categories must be non-empty")
    if not isinstance(draft.items, list) or not draft.items:
        raise ChecklistValidationError("items must be non-empty")

    category_ids: set[str] = set()
    for category in draft.categories:
        if not isinstance(category, ChecklistCategoryDraft):
            raise ChecklistValidationError(
                "categories entries must be ChecklistCategoryDraft"
            )
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
        _require_nonnegative_int(category.sort_order, "category sort_order")

    item_ids: set[str] = set()
    normalized_items: set[tuple[str, str]] = set()
    valid_admin_config_ids = {
        config_item["id"]
        for config_item in admin_configs
        if isinstance(config_item, dict)
        and isinstance(config_item.get("id"), int)
        and not isinstance(config_item["id"], bool)
        and config_item["id"] >= 0
    }
    for item in draft.items:
        if not isinstance(item, ChecklistItemDraft):
            raise ChecklistValidationError(
                "items entries must be ChecklistItemDraft"
            )
        _require_nonempty_string(item.id, "item id")
        if item.id in item_ids:
            raise ChecklistValidationError("item local id must be unique")
        item_ids.add(item.id)
        _require_nonempty_string(item.category_id, "item category_id")
        if item.category_id not in category_ids:
            raise ChecklistValidationError(
                f"item {item.id} references an unknown category"
            )

        _require_nonempty_string(item.title, "item title")
        _require_nonempty_string(item.requirement, "item requirement")
        _require_nonempty_string(item.technique, "item technique")
        _require_nonempty_string(item.importance, "item importance")
        _require_nonnegative_int(item.sort_order, "item sort_order")
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
        _require_nonempty_string(
            item.source_citations,
            f"item {item.id} source_citations",
        )
        _require_string_list(item.retrieval_hints, "item retrieval_hints")
        _require_nonempty_string(
            item.expected_evidence,
            f"item {item.id} expected_evidence",
        )
        _require_nonempty_string(
            item.compliance_rules,
            f"item {item.id} compliance_rules",
        )
        _require_nonempty_string(
            item.consequence_rules,
            f"item {item.id} consequence_rules",
        )
        if not isinstance(item.admin_config_refs, list):
            raise ChecklistValidationError(
                "item admin_config_refs must be list[int]"
            )
        for reference in item.admin_config_refs:
            _require_nonnegative_int(reference, "item admin_config_refs entry")
            if reference not in valid_admin_config_ids:
                raise ChecklistValidationError(
                    "item admin_config_refs contains an unknown config id"
                )
        content_source = item.content_source or "precise_search"
        if content_source not in _CONTENT_SOURCE_VALUES:
            raise ChecklistValidationError(
                f"item {item.id} content_source is invalid"
            )
        content_target = item.content_target if isinstance(item.content_target, dict) else {}
        _validate_content_target(content_source, content_target, item.id)

    if not isinstance(draft.raw_response, dict):
        raise ChecklistValidationError("raw_response must be a dict")
    try:
        serialize_checklist_json(draft.raw_response)
    except Exception as exc:
        raise ChecklistValidationError(
            "raw_response must be JSON serializable"
        ) from exc


def _global_id(prefix: str, generation_id: int, local_id: str) -> str:
    digest = hashlib.sha1(local_id.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{generation_id}-{digest}"


def _safe_repr(value: Any, limit: int = 10_000) -> str:
    try:
        rendered = repr(value)
    except Exception as exc:
        rendered = f"<repr failed: {type(exc).__name__}: {exc}>"
    if len(rendered) > limit:
        return f"{rendered[:limit]}...<truncated>"
    return rendered


def _raw_artifact_payload(draft: ChecklistDraft) -> dict[str, Any]:
    try:
        payload = asdict(draft)
        serialize_checklist_json(payload)
    except Exception as exc:
        return {
            "serialization_error": f"{type(exc).__name__}: {exc}",
            "safe_repr": _safe_repr(draft),
        }
    return payload


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

    categories = [asdict(category) for category in draft.categories]
    for category in categories:
        category["id"] = category_map[category["id"]]
    items = [asdict(item) for item in draft.items]
    for item in items:
        item["id"] = item_map[item["id"]]
        item["category_id"] = category_map[item["category_id"]]
        item["diagnosis_mode"] = _normalize_diagnosis_mode(item.get("diagnosis_mode"))
    payload = {
        "schema_version": draft.schema_version,
        "categories": categories,
        "items": items,
    }
    return payload, category_map, item_map


async def recover_checklist_publications() -> None:
    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(ChecklistGeneration, DiagnosisTask)
                .join(
                    DiagnosisTask,
                    ChecklistGeneration.task_id == DiagnosisTask.id,
                )
            )
        ).all()

    for generation, task in rows:
        filename = f"checklist-generation-{generation.id}.json"
        final_path = checklist_json_path(task.id, filename)
        staged_path = staged_checklist_path(task.id, filename)
        is_current_success = (
            generation.status == "succeeded"
            and task.current_checklist_generation_id == generation.id
        )
        if not is_current_success:
            remove_staged_checklist(staged_path)
            continue
        if final_path.exists():
            remove_staged_checklist(staged_path)
        elif staged_path.exists():
            promote_staged_checklist_json(
                task.id,
                staged_path,
                filename,
            )


class ChecklistService:
    def __init__(self, agent: ChecklistAgent):
        self.agent = agent

    async def generate_for_task(self, task_id: str) -> int:
        async with _task_generation_lock(task_id):
            await self._ensure_not_published(task_id)
            return await self._generate_locked(task_id)

    async def _ensure_not_published(self, task_id: str) -> None:
        async with db.SessionLocal() as session:
            task = await session.get(DiagnosisTask, task_id)
            if (
                task is not None
                and task.current_checklist_generation_id is not None
            ):
                raise ChecklistValidationError("checklist already published")

    async def _generate_locked(self, task_id: str) -> int:
        generation_id: int | None = None
        staged_path = None
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
                    context.system_instructions
                    + context.interpret_report
                    + context.admin_config
                    + "".join(context.segments)
                ).encode("utf-8")
            ).hexdigest()
            generation_id = await self._create_attempt(
                task_id,
                input_hash,
                admin_configs,
            )

            draft = await self.agent.generate(task_id=task_id, context=context)
            raw_path = write_checklist_debug_json(
                task_id,
                f"checklist-generation-{generation_id}-raw.json",
                _raw_artifact_payload(draft),
            )
            await self._save_raw_path(generation_id, raw_path.as_posix())

            validate_draft(
                draft,
                context,
                tender_markdown,
                admin_configs,
            )
            payload, category_map, item_map = _build_published_payload(
                draft,
                generation_id,
            )
            formal_filename = f"checklist-generation-{generation_id}.json"
            staged_path = stage_checklist_json(
                task_id,
                formal_filename,
                payload,
            )
            final_path = checklist_json_path(task_id, formal_filename)
            await self._publish(
                task_id,
                generation_id,
                draft,
                category_map,
                item_map,
            )
            try:
                promote_staged_checklist_json(
                    task_id,
                    staged_path,
                    formal_filename,
                )
            except Exception as exc:
                logger.exception(
                    "Checklist artifact promotion failed for task %s",
                    task_id,
                )
                compensated = False
                try:
                    await asyncio.shield(
                        self._compensate_publish(task_id, generation_id)
                    )
                    compensated = True
                except Exception:
                    logger.exception(
                        "Checklist publish compensation failed for task %s",
                        task_id,
                    )
                finally:
                    final_path.unlink(missing_ok=True)
                if compensated:
                    remove_staged_checklist(staged_path)
                raise RuntimeError(
                    "checklist_artifact_publish_failed"
                ) from exc
            return generation_id
        except asyncio.CancelledError as cancelled_error:
            cleanup_task = asyncio.create_task(
                self._fail(
                    task_id,
                    generation_id,
                    public_message="checklist_generation_cancelled",
                )
            )
            try:
                await _wait_for_cleanup_task(cleanup_task)
            except BaseException:
                logger.exception(
                    "Checklist cancellation cleanup failed for task %s",
                    task_id,
                )
            else:
                if staged_path is not None:
                    remove_staged_checklist(staged_path)
            raise cancelled_error
        except Exception as exc:
            logger.exception("Checklist generation failed for task %s", task_id)
            await self._fail(task_id, generation_id, error=exc)
            if staged_path is not None:
                remove_staged_checklist(staged_path)
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
                            source_references=item.source_citations,
                            retrieval_hints=json.dumps(
                                item.retrieval_hints,
                                ensure_ascii=False,
                            ),
                            expected_evidence=item.expected_evidence,
                            compliance_rules=item.compliance_rules,
                            consequence_rules=item.consequence_rules,
                            admin_config_refs=json.dumps(item.admin_config_refs),
                            content_source=item.content_source or "precise_search",
                            content_target=json.dumps(
                                item.content_target or {},
                                ensure_ascii=False,
                            ),
                            diagnosis_mode=_normalize_diagnosis_mode(
                                item.diagnosis_mode
                            ),
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

    async def _compensate_publish(
        self,
        task_id: str,
        generation_id: int,
    ) -> None:
        now = utcnow()
        async with db.SessionLocal() as session:
            async with session.begin():
                await session.execute(
                    delete(ChecklistItem).where(
                        ChecklistItem.generation_id == generation_id
                    )
                )
                await session.execute(
                    delete(ChecklistCategory).where(
                        ChecklistCategory.generation_id == generation_id
                    )
                )
                generation = await session.get(
                    ChecklistGeneration,
                    generation_id,
                )
                if generation is not None:
                    generation.status = "failed"
                    generation.error_message = "checklist_generation_failed"
                    generation.finished_at = now
                task = await session.get(DiagnosisTask, task_id)
                if task is not None:
                    if task.current_checklist_generation_id == generation_id:
                        task.current_checklist_generation_id = None
                    task.status = "failed"
                    task.error_message = "checklist_generation_failed"
                    task.failure_stage = "checklist_generation"
                    task.progress_done = 0
                    task.progress_total = 0
                    task.finished_at = now
                    task.updated_at = now

    async def _fail(
        self,
        task_id: str,
        generation_id: int | None,
        error: Exception | None = None,
        *,
        public_message: str | None = None,
    ) -> None:
        if public_message is None:
            if isinstance(error, ChecklistValidationError):
                public_message = "checklist_validation_failed"
            else:
                public_message = "checklist_generation_failed"
        public_message = public_message[:_MAX_PUBLIC_ERROR_LENGTH]
        now = utcnow()
        async with db.SessionLocal() as session:
            async with session.begin():
                if generation_id is not None:
                    await session.execute(
                        delete(ChecklistItem).where(
                            ChecklistItem.generation_id == generation_id
                        )
                    )
                    await session.execute(
                        delete(ChecklistCategory).where(
                            ChecklistCategory.generation_id == generation_id
                        )
                    )
                    generation = await session.get(
                        ChecklistGeneration,
                        generation_id,
                    )
                    if generation is not None:
                        generation.status = "failed"
                        generation.error_message = public_message
                        generation.finished_at = now
                else:
                    await session.execute(
                        update(ChecklistGeneration)
                        .where(
                            ChecklistGeneration.task_id == task_id,
                            ChecklistGeneration.status == "generating",
                        )
                        .values(
                            status="failed",
                            error_message=public_message,
                            finished_at=now,
                        )
                    )
                task = await session.get(DiagnosisTask, task_id)
                if task is not None:
                    if task.current_checklist_generation_id == generation_id:
                        task.current_checklist_generation_id = None
                    task.status = "failed"
                    task.error_message = public_message
                    task.failure_stage = failure_stage_for_error(
                        error,
                        public_message=public_message,
                    )
                    task.finished_at = now
                    task.updated_at = now
