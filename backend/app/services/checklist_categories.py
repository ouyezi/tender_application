from __future__ import annotations

from app.engine.base import ChecklistCategoryDraft

_FIXED = (
    ("cat_001", "废标红线", "导致否决/不予受理的重大偏差", "废标 否决 重大偏差 无效投标"),
    ("cat_002", "资质文件", "资格证明文件与合规材料", "资质 资格 营业执照 业绩 财务"),
    ("cat_003", "格式要求", "编制、签署、封装等形式要求", "格式 签字 盖章 密封 目录"),
    ("cat_004", "得分检查", "影响评分的响应与填报项", "得分 评分 折扣率 报价"),
    ("cat_005", "风险检查", "履约/一致性与潜在争议点", "风险 履约 一致性"),
    ("cat_006", "其他检查", "未归入上述类别的必要检查项", "其他 补充"),
)

FIXED_CATEGORY_IDS = frozenset(row[0] for row in _FIXED)


def fixed_categories_draft() -> list[ChecklistCategoryDraft]:
    return [
        ChecklistCategoryDraft(
            id=cat_id,
            name=name,
            description=description,
            retrieval_query=retrieval_query,
            expected_locations=[],
            sort_order=index,
        )
        for index, (cat_id, name, description, retrieval_query) in enumerate(
            _FIXED, start=1
        )
    ]
