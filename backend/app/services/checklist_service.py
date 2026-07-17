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
_MAX_PUBLIC_ERROR_LENGTH = 240
logger = logging.getLogger(__name__)


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


def _require_rules(value: Any, field: str, allowed_keys: set[str]) -> None:
    if not isinstance(value, dict) or not value:
        raise ChecklistValidationError(f"{field} must be a non-empty dict[str, str]")
    if any(
        not isinstance(key, str)
        or not isinstance(rule, str)
        or not rule.strip()
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
    category_counts = {category_id: 0 for category_id in category_ids}
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
        category_counts[item.category_id] += 1
        if category_counts[item.category_id] > config.CHECKLIST_MAX_ITEMS_PER_CATEGORY:
            raise ChecklistValidationError(
                "category exceeds maximum items per category"
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
        if not isinstance(item.source_references, list) or not item.source_references:
            raise ChecklistValidationError(
                f"item {item.id} source_references must be non-empty"
            )
        for reference in item.source_references:
            _validate_source_reference(reference, context, tender_markdown, item.id)
        _require_string_list(item.retrieval_hints, "item retrieval_hints")
        _require_string_list(item.expected_evidence, "item expected_evidence")
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
                    task.finished_at = now
                    task.updated_at = now
