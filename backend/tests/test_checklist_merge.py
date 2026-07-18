from app.engine.base import ChecklistCategoryDraft, ChecklistDraft, ChecklistItemDraft
from app.engine.checklist_merge import merge_checklist_drafts


def _item(item_id, category_id, title, requirement, section="正文", segment_index=0):
    return ChecklistItemDraft(
        id=item_id,
        category_id=category_id,
        title=title,
        requirement=requirement,
        technique=f"核对{title}",
        importance="high",
        source_references=[
            {
                "section": section,
                "start": 0,
                "end": 1,
                "segment_index": segment_index,
                "coordinate_space": "segment",
            }
        ],
        retrieval_hints=[title],
        expected_evidence=[title],
        compliance_rules={
            "satisfied": "ok",
            "violated": "bad",
            "cannot_satisfy": "no",
            "insufficient_evidence": "缺少",
        },
        consequence_rules={"general_risk": "风险"},
        admin_config_refs=[],
        sort_order=1,
    )


def test_merge_dedupes_items_and_rewrites_ids():
    draft_a = ChecklistDraft(
        schema_version="1",
        categories=[
            ChecklistCategoryDraft(
                id="c-a",
                name="资格证明材料",
                description="资格",
                retrieval_query="资格",
                expected_locations=["资格"],
                sort_order=1,
            )
        ],
        items=[_item("i-a", "c-a", "营业执照", "须提供营业执照", segment_index=0)],
        raw_response={"segment": 0},
    )
    draft_b = ChecklistDraft(
        schema_version="1",
        categories=[
            ChecklistCategoryDraft(
                id="c-b",
                name="资格证明材料",
                description="应被忽略的二次描述",
                retrieval_query="证照",
                expected_locations=["证照"],
                sort_order=1,
            )
        ],
        items=[
            _item("i-b1", "c-b", "营业执照", "须提供营业执照", segment_index=1),
            _item("i-b2", "c-b", "资质证书", "须提供资质", "资质", 1),
        ],
        raw_response={"segment": 1},
    )

    merged = merge_checklist_drafts([draft_a, draft_b], max_items_per_category=20)

    assert [c.name for c in merged.categories] == ["资格证明材料"]
    assert merged.categories[0].id == "category-001"
    assert "资格" in merged.categories[0].retrieval_query
    assert "证照" in merged.categories[0].retrieval_query
    assert {item.title for item in merged.items} == {"营业执照", "资质证书"}
    assert [item.id for item in merged.items] == ["item-001", "item-002"]
    assert all(item.category_id == "category-001" for item in merged.items)
    assert "segments" in merged.raw_response
    assert "merged" in merged.raw_response


def test_merge_splits_oversized_category_by_section():
    category = ChecklistCategoryDraft(
        id="c1",
        name="综合响应材料",
        description="综合",
        retrieval_query="综合",
        expected_locations=[],
        sort_order=1,
    )
    items = [
        _item(f"i{i}", "c1", f"标题{i}", f"要求{i}", section=f"章节{i // 2}", segment_index=0)
        for i in range(5)
    ]
    draft = ChecklistDraft(
        schema_version="1",
        categories=[category],
        items=items,
        raw_response={},
    )
    merged = merge_checklist_drafts([draft], max_items_per_category=2)
    assert len(merged.categories) >= 3
    assert all(
        sum(1 for item in merged.items if item.category_id == category.id) <= 2
        for category in merged.categories
    )
