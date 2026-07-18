from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)

_NORMALIZE = re.compile(r"\s+")


def _norm(value: str) -> str:
    return _NORMALIZE.sub("", value).casefold()


def merge_checklist_drafts(
    drafts: list[ChecklistDraft],
    *,
    max_items_per_category: int,
) -> ChecklistDraft:
    if not drafts:
        raise ValueError("drafts must be non-empty")

    category_by_name: dict[str, dict[str, Any]] = {}
    items: list[tuple[str, ChecklistItemDraft]] = []
    seen_items: set[tuple[str, str]] = set()
    segment_raw: list[Any] = []

    for draft in drafts:
        segment_raw.append(draft.raw_response)
        local_name = {c.id: c.name for c in draft.categories}
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
    for key, meta in category_by_name.items():
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
        if len(bucket) <= max_items_per_category:
            groups = [(base_name, bucket)]
        else:
            by_section: dict[str, list[ChecklistItemDraft]] = {}
            for item in bucket:
                section = "未标注"
                if item.source_references:
                    raw_section = item.source_references[0].get("section")
                    if isinstance(raw_section, str) and raw_section.strip():
                        section = raw_section.strip()
                by_section.setdefault(section, []).append(item)
            groups = [
                (f"{base_name}·{section}", section_items)
                for section, section_items in by_section.items()
            ]
        for group_name, group_items in groups:
            cat_id = f"category-{len(final_categories) + 1:03d}"
            final_categories.append(
                ChecklistCategoryDraft(
                    id=cat_id,
                    name=group_name,
                    description=meta["description"],
                    retrieval_query=" ".join(meta["retrieval_parts"]),
                    expected_locations=list(meta["locations"]),
                    sort_order=len(final_categories) + 1,
                )
            )
            for item in group_items:
                final_items.append(
                    ChecklistItemDraft(
                        id=f"item-{len(final_items) + 1:03d}",
                        category_id=cat_id,
                        title=item.title,
                        requirement=item.requirement,
                        technique=item.technique,
                        importance=item.importance,
                        source_references=list(item.source_references),
                        retrieval_hints=list(item.retrieval_hints),
                        expected_evidence=list(item.expected_evidence),
                        compliance_rules=dict(item.compliance_rules),
                        consequence_rules=dict(item.consequence_rules),
                        admin_config_refs=list(item.admin_config_refs),
                        sort_order=len(final_items) + 1,
                    )
                )

    merged_payload = {
        "schema_version": drafts[0].schema_version,
        "categories": [asdict(c) for c in final_categories],
        "items": [asdict(i) for i in final_items],
    }
    return ChecklistDraft(
        schema_version=drafts[0].schema_version,
        categories=final_categories,
        items=final_items,
        raw_response={"segments": segment_raw, "merged": merged_payload},
    )
