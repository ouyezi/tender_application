from app.engine.base import ChecklistCategoryDraft, ChecklistDraft, ChecklistItemDraft
from app.engine.checklist_merge import merge_checklist_drafts


def _item(item_id, category_id, title, requirement, section="正文", segment_index=0):
    del segment_index
    return ChecklistItemDraft(
        id=item_id,
        category_id=category_id,
        title=title,
        requirement=requirement,
        technique=f"核对{title}",
        importance="high",
        source_citations=f"- 章节：{section}",
        retrieval_hints=[title],
        expected_evidence=f"- {title}",
        compliance_rules="## 满足\nok\n\n## 违反\nbad\n\n## 不能满足\nno\n\n## 证据不足\n缺少",
        consequence_rules="[general_risk]\n风险",
        admin_config_refs=[],
        sort_order=1,
    )


def test_merge_dedupes_items_and_rewrites_ids():
    draft_a = ChecklistDraft(
        schema_version="2",
        categories=[
            ChecklistCategoryDraft(
                id="cat_001",
                name="废标红线",
                description="废标",
                retrieval_query="废标",
                expected_locations=[],
                sort_order=1,
            )
        ],
        items=[_item("i-a", "cat_001", "营业执照", "须提供营业执照")],
        raw_response={"segment": 0},
    )
    draft_b = ChecklistDraft(
        schema_version="2",
        categories=[
            ChecklistCategoryDraft(
                id="cat_001",
                name="废标红线",
                description="应被忽略的二次描述",
                retrieval_query="证照",
                expected_locations=[],
                sort_order=1,
            )
        ],
        items=[
            _item("i-b1", "cat_001", "营业执照", "须提供营业执照"),
            _item("i-b2", "cat_001", "资质证书", "须提供资质", "资质"),
        ],
        raw_response={"segment": 1},
    )

    merged = merge_checklist_drafts([draft_a, draft_b])

    assert [c.name for c in merged.categories] == ["废标红线"]
    assert merged.categories[0].id == "category-001"
    assert {item.title for item in merged.items} == {"营业执照", "资质证书"}
    assert [item.id for item in merged.items] == ["item-001", "item-002"]
    assert all(item.category_id == "category-001" for item in merged.items)
    assert "segments" in merged.raw_response
    assert "merged" in merged.raw_response


def test_merge_keeps_single_category_without_splitting():
    items = [
        _item(f"i{i}", "cat_003", f"标题{i}", f"要求{i}", section="同一章节")
        for i in range(5)
    ]
    draft = ChecklistDraft(
        schema_version="2",
        categories=[
            ChecklistCategoryDraft(
                id="cat_003",
                name="格式要求",
                description="格式",
                retrieval_query="格式",
                expected_locations=[],
                sort_order=3,
            )
        ],
        items=items,
        raw_response={},
    )
    merged = merge_checklist_drafts([draft])
    assert len(merged.categories) == 1
    assert merged.categories[0].name == "格式要求"
    assert len(merged.items) == 5


def test_merge_keeps_many_items_in_one_fixed_category():
    items = [
        _item(
            f"i{i}",
            "cat_001",
            f"标题{i}",
            f"要求{i}",
            section="第三章 评审办法 5.3",
        )
        for i in range(25)
    ]
    draft = ChecklistDraft(
        schema_version="2",
        categories=[],
        items=items,
        raw_response={},
    )
    merged = merge_checklist_drafts([draft])
    assert len(merged.categories) == 1
    assert merged.categories[0].name == "废标红线"
    assert len(merged.items) == 25


def test_merge_groups_by_fixed_category_id():
    draft_a = ChecklistDraft(
        schema_version="2",
        categories=[],
        items=[_item("i1", "cat_001", "红线A", "要求A")],
        raw_response={},
    )
    draft_b = ChecklistDraft(
        schema_version="2",
        categories=[],
        items=[
            _item("i2", "cat_001", "红线B", "要求B"),
            _item("i3", "cat_004", "得分C", "要求C"),
        ],
        raw_response={},
    )
    merged = merge_checklist_drafts([draft_a, draft_b])
    names = {category.name for category in merged.categories}
    assert "废标红线" in names
    assert "得分检查" in names
    assert len(merged.items) == 3
