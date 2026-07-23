from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.services.checklist_categories import fixed_categories_draft

_NORMALIZE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _NORMALIZE.sub("", value).casefold()


def _clone_item(
    item: ChecklistItemDraft,
    *,
    item_id: str,
    category_id: str,
    sort_order: int,
) -> ChecklistItemDraft:
    return ChecklistItemDraft(
        id=item_id,
        category_id=category_id,
        title=item.title,
        requirement=item.requirement,
        technique=item.technique,
        importance=item.importance,
        source_citations=item.source_citations,
        retrieval_hints=list(item.retrieval_hints),
        expected_evidence=item.expected_evidence,
        compliance_rules=item.compliance_rules,
        consequence_rules=item.consequence_rules,
        admin_config_refs=list(item.admin_config_refs),
        sort_order=sort_order,
        content_source=item.content_source or "precise_search",
        content_target=dict(item.content_target or {}),
        diagnosis_mode=item.diagnosis_mode or "file",
    )


def _append_category_items(
    *,
    final_categories: list[ChecklistCategoryDraft],
    final_items: list[ChecklistItemDraft],
    name: str,
    description: str,
    retrieval_query: str,
    expected_locations: list[str],
    bucket: list[ChecklistItemDraft],
) -> None:
    if not bucket:
        return
    cat_id = f"category-{len(final_categories) + 1:03d}"
    final_categories.append(
        ChecklistCategoryDraft(
            id=cat_id,
            name=name,
            description=description,
            retrieval_query=retrieval_query,
            expected_locations=list(expected_locations),
            sort_order=len(final_categories) + 1,
        )
    )
    for item in bucket:
        final_items.append(
            _clone_item(
                item,
                item_id=f"item-{len(final_items) + 1:03d}",
                category_id=cat_id,
                sort_order=len(final_items) + 1,
            )
        )


def _merge_v2_drafts(drafts: list[ChecklistDraft]) -> ChecklistDraft:
    fixed = fixed_categories_draft()
    fixed_by_id = {category.id: category for category in fixed}
    fixed_order = [category.id for category in fixed]
    buckets: dict[str, list[ChecklistItemDraft]] = {
        category_id: [] for category_id in fixed_order
    }
    seen_items: set[tuple[str, str]] = set()
    segment_raw: list[Any] = []

    for draft in drafts:
        segment_raw.append(draft.raw_response)
        for item in draft.items:
            dedupe = (_norm(item.title), _norm(item.requirement))
            if dedupe in seen_items:
                continue
            seen_items.add(dedupe)
            bucket_id = item.category_id
            if bucket_id not in buckets:
                bucket_id = "cat_006"
            buckets[bucket_id].append(item)

    final_categories: list[ChecklistCategoryDraft] = []
    final_items: list[ChecklistItemDraft] = []
    for fixed_cat_id in fixed_order:
        bucket = buckets[fixed_cat_id]
        if not bucket:
            continue
        meta = fixed_by_id[fixed_cat_id]
        _append_category_items(
            final_categories=final_categories,
            final_items=final_items,
            name=meta.name,
            description=meta.description,
            retrieval_query=meta.retrieval_query,
            expected_locations=list(meta.expected_locations),
            bucket=bucket,
        )

    merged_payload = {
        "schema_version": "2",
        "categories": [asdict(category) for category in final_categories],
        "items": [asdict(item) for item in final_items],
    }
    return ChecklistDraft(
        schema_version="2",
        categories=final_categories,
        items=final_items,
        raw_response={"segments": segment_raw, "merged": merged_payload},
    )


def _merge_v1_drafts(drafts: list[ChecklistDraft]) -> ChecklistDraft:
    category_by_name: dict[str, dict[str, Any]] = {}
    items: list[tuple[str, ChecklistItemDraft]] = []
    seen_items: set[tuple[str, str]] = set()
    segment_raw: list[Any] = []

    for draft in drafts:
        segment_raw.append(draft.raw_response)
        local_name = {category.id: category.name for category in draft.categories}
        for category in draft.categories:
            key = _norm(category.name)
            existing = category_by_name.get(key)
            if existing is None:
                category_by_name[key] = {
                    "name": category.name,
                    "description": category.description,
                    "retrieval_parts": [category.retrieval_query],
                    "locations": list(category.expected_locations),
                }
            else:
                if category.retrieval_query not in existing["retrieval_parts"]:
                    existing["retrieval_parts"].append(category.retrieval_query)
                for loc in category.expected_locations:
                    if loc not in existing["locations"]:
                        existing["locations"].append(loc)
        for item in draft.items:
            dedupe = (_norm(item.title), _norm(item.requirement))
            if dedupe in seen_items:
                continue
            seen_items.add(dedupe)
            items.append((local_name[item.category_id], item))

    name_order = list(dict.fromkeys(name for name, _ in items))
    for _key, meta in category_by_name.items():
        if meta["name"] not in name_order:
            name_order.append(meta["name"])

    buckets: dict[str, list[ChecklistItemDraft]] = {name: [] for name in name_order}
    for name, item in items:
        buckets.setdefault(name, []).append(item)

    final_categories: list[ChecklistCategoryDraft] = []
    final_items: list[ChecklistItemDraft] = []
    for base_name, bucket in buckets.items():
        if not bucket:
            continue
        meta = category_by_name[_norm(base_name)]
        _append_category_items(
            final_categories=final_categories,
            final_items=final_items,
            name=base_name,
            description=meta["description"],
            retrieval_query=" ".join(meta["retrieval_parts"]),
            expected_locations=list(meta["locations"]),
            bucket=bucket,
        )

    merged_payload = {
        "schema_version": drafts[0].schema_version,
        "categories": [asdict(category) for category in final_categories],
        "items": [asdict(item) for item in final_items],
    }
    return ChecklistDraft(
        schema_version=drafts[0].schema_version,
        categories=final_categories,
        items=final_items,
        raw_response={"segments": segment_raw, "merged": merged_payload},
    )


def merge_checklist_drafts(drafts: list[ChecklistDraft]) -> ChecklistDraft:
    if not drafts:
        raise ValueError("drafts must be non-empty")
    if drafts[0].schema_version == "2":
        return _merge_v2_drafts(drafts)
    return _merge_v1_drafts(drafts)
