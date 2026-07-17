import json

import pytest

from app.engine.base import ChecklistAgent
from app.engine.checklist_mock import MockChecklistAgent
from app.services.checklist_context import PromptCall, PromptContext


def _context(*segments: str) -> PromptContext:
    prefix = "固定规则与 schema"
    return PromptContext(
        stable_prefix=prefix,
        segments=list(segments),
        calls=[
            PromptCall(stable_prefix=prefix, tender_segment=segment)
            for segment in segments
        ],
    )


@pytest.mark.asyncio
async def test_generate_returns_complete_schema_and_valid_references():
    agent: ChecklistAgent = MockChecklistAgent()

    draft = await agent.generate(
        task_id="TASK-1",
        context=_context("# 营业执照要求\n投标人须提供有效营业执照复印件。"),
    )

    assert draft.schema_version == "1"
    assert draft.categories
    assert draft.items
    category_ids = {category.id for category in draft.categories}
    assert len(category_ids) == len(draft.categories)
    assert len({item.id for item in draft.items}) == len(draft.items)
    for item in draft.items:
        assert item.category_id in category_ids
        assert item.title
        assert item.requirement
        assert item.technique
        assert item.importance in {"high", "medium", "low"}
        assert item.source_references
        assert item.retrieval_hints
        assert item.expected_evidence
        assert item.compliance_rules
        assert set(item.compliance_rules) <= {
            "satisfied",
            "violated",
            "cannot_satisfy",
            "insufficient_evidence",
        }
        assert item.consequence_rules
        assert set(item.consequence_rules) <= {
            "no_score",
            "bid_unusable",
            "score_risk",
            "general_risk",
        }
        for reference in item.source_references:
            assert {"section", "start", "end"} <= reference.keys()
            assert reference["start"] >= 0
            assert reference["end"] > reference["start"]


@pytest.mark.asyncio
async def test_generate_records_same_prefix_for_every_segment():
    context = _context("# 第一章\n资格要求。", "# 第二章\n技术参数。")
    agent = MockChecklistAgent()

    await agent.generate(task_id="TASK-2", context=context)

    assert agent.prompt_prefixes == [context.stable_prefix] * len(context.calls)


@pytest.mark.asyncio
async def test_generate_deduplicates_repeated_overlap_titles():
    agent = MockChecklistAgent()
    context = _context(
        "# 资格要求\n投标人须具备有效资质。",
        "# 资格要求\n投标人须具备有效资质。\n补充说明。",
    )

    draft = await agent.generate(task_id="TASK-3", context=context)

    normalized_titles = {
        "".join(title.split()).casefold() for title in (item.title for item in draft.items)
    }
    assert len(draft.items) == len(normalized_titles) == 1


@pytest.mark.asyncio
async def test_generate_creates_only_categories_used_by_content():
    agent = MockChecklistAgent()
    context = _context(
        "# 资格审查\n须提交营业执照和资质证书。",
        "# 评分办法\n企业业绩最高得分为十分。",
        "# 技术方案\n技术参数须逐项响应。",
    )

    draft = await agent.generate(task_id="TASK-4", context=context)

    assert {category.name for category in draft.categories} == {
        "资格证明材料",
        "商务评分材料",
        "技术响应材料",
    }
    assert "综合响应材料" not in {category.name for category in draft.categories}


@pytest.mark.asyncio
async def test_generate_uses_general_category_for_text_without_category_keywords():
    agent = MockChecklistAgent()

    draft = await agent.generate(
        task_id="TASK-GENERAL",
        context=_context("# 其他要求\n投标文件应按规定装订并提交"),
    )

    assert {category.name for category in draft.categories} == {"综合响应材料"}
    assert draft.items[0].category_id == draft.categories[0].id


@pytest.mark.asyncio
async def test_generate_is_deterministic_for_same_input():
    context = _context("# 投标要求\n投标文件须按时递交。")

    first = await MockChecklistAgent().generate(task_id="TASK-5", context=context)
    second = await MockChecklistAgent().generate(task_id="TASK-5", context=context)

    assert first == second


@pytest.mark.asyncio
async def test_generate_empty_body_returns_valid_fallback_item():
    draft = await MockChecklistAgent().generate(
        task_id="TASK-6",
        context=_context(""),
    )

    assert draft.categories
    assert len(draft.items) == 1
    assert draft.items[0].title == "全文完整性检查"
    assert draft.items[0].source_references[0]["end"] > 0


@pytest.mark.asyncio
async def test_raw_response_is_json_serializable_and_contains_generated_data():
    draft = await MockChecklistAgent().generate(
        task_id="TASK-7",
        context=_context("# 技术参数\n须提供参数响应表。"),
    )

    serialized = json.dumps(draft.raw_response, ensure_ascii=False)

    assert serialized
    assert draft.raw_response["schema_version"] == "1"
    assert draft.raw_response["categories"]
    assert draft.raw_response["items"]
