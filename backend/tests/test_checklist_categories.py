from app.services.checklist_categories import (
    FIXED_CATEGORY_IDS,
    fixed_categories_draft,
)


def test_fixed_categories_has_six_entries_in_order():
    drafts = fixed_categories_draft()
    assert len(drafts) == 6
    assert [c.id for c in drafts] == [
        "cat_001",
        "cat_002",
        "cat_003",
        "cat_004",
        "cat_005",
        "cat_006",
    ]
    assert drafts[0].name == "废标红线"
    assert drafts[0].sort_order == 1


def test_fixed_category_ids_is_frozenset():
    assert "cat_001" in FIXED_CATEGORY_IDS
    assert "cat_999" not in FIXED_CATEGORY_IDS
