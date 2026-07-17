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
    segment = "# 营业执照要求\n投标人须提供有效营业执照复印件。"

    draft = await agent.generate(
        task_id="TASK-1",
        context=_context(segment),
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
            assert reference["coordinate_space"] == "segment"
            assert reference["start"] >= 0
            assert reference["end"] > reference["start"]
            assert segment[reference["start"] : reference["end"]] == item.requirement


@pytest.mark.asyncio
async def test_generate_records_same_prefix_for_every_segment():
    context = _context("# 第一章\n资格要求。", "# 第二章\n技术参数。")
    agent = MockChecklistAgent()

    await agent.generate(task_id="TASK-2", context=context)

    assert agent.prompt_prefixes == [context.stable_prefix] * len(context.calls)


@pytest.mark.asyncio
async def test_generate_deduplicates_identical_overlap_candidates():
    agent = MockChecklistAgent()
    context = _context(
        "# 资格要求\n投标人须具备有效资质。",
        "# 资格要求\n投标人须具备有效资质。",
    )

    draft = await agent.generate(task_id="TASK-3", context=context)

    assert len(draft.items) == 1


@pytest.mark.asyncio
async def test_generate_preserves_same_title_with_different_requirements():
    agent = MockChecklistAgent()
    context = _context(
        "# 资格要求\n投标人须具备甲级资质。",
        "# 资格要求\n项目负责人须具备注册证书。",
    )

    draft = await agent.generate(task_id="TASK-SAME-TITLE", context=context)
    repeated = await MockChecklistAgent().generate(
        task_id="TASK-SAME-TITLE",
        context=context,
    )

    assert [item.title for item in draft.items] == ["资格要求", "资格要求"]
    assert {item.requirement for item in draft.items} == {
        "投标人须具备甲级资质。",
        "项目负责人须具备注册证书。",
    }
    assert len({item.id for item in draft.items}) == 2
    assert [item.id for item in draft.items] == [item.id for item in repeated.items]


@pytest.mark.asyncio
async def test_generate_extracts_all_titled_sections_from_one_segment():
    agent = MockChecklistAgent()
    segment = """# 资格审查
须提交营业执照和资质证书。
# 评分办法
企业业绩最高得分为十分。
# 技术方案
技术参数须逐项响应。
"""

    draft = await agent.generate(
        task_id="TASK-MULTI-SECTION",
        context=_context(segment),
    )

    assert {category.name for category in draft.categories} == {
        "资格证明材料",
        "商务评分材料",
        "技术响应材料",
    }
    assert {item.title for item in draft.items} == {"资格审查", "评分办法", "技术方案"}
    assert len(draft.items) == 3


@pytest.mark.asyncio
async def test_generate_extracts_multiple_untitled_sentences():
    agent = MockChecklistAgent()

    draft = await agent.generate(
        task_id="TASK-UNTITLED",
        context=_context("投标文件须密封提交。装订方式须符合规定。"),
    )

    assert [item.requirement for item in draft.items] == [
        "投标文件须密封提交。",
        "装订方式须符合规定。",
    ]


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
    reference = draft.items[0].source_references[0]
    assert reference == {
        "section": "全文",
        "start": 0,
        "end": 1,
        "segment_index": 0,
        "coordinate_space": "synthetic",
        "synthetic": True,
    }


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


@pytest.mark.asyncio
async def test_mock_agent_infers_content_source_heuristics():
    agent = MockChecklistAgent()

    full_doc = await agent.generate(
        task_id="TASK-CS-1",
        context=_context("# 招标文件全文\n须完整阅读招标全文要求。"),
    )
    full_item = next(i for i in full_doc.items if "全文" in i.title or "全文" in i.requirement)
    assert full_item.content_source == "full_document"
    assert full_item.content_target == {"file_role": "tender"}

    qual = await agent.generate(
        task_id="TASK-CS-2",
        context=_context("# 授权书\n投标人须提供有效授权证书。"),
    )
    qual_item = qual.items[0]
    assert qual_item.content_source == "collection"
    assert "授权证书" in qual_item.content_target.get("target_tags", [])

    bid = await agent.generate(
        task_id="TASK-CS-3",
        context=_context("# 标书全文\n须提交标书全文响应。"),
    )
    bid_item = bid.items[0]
    assert bid_item.content_source == "large_segments"
    assert bid_item.content_target == {"file_role": "bid"}

    precise = await agent.generate(
        task_id="TASK-CS-4",
        context=_context("# 技术参数\n须提供参数响应表。"),
    )
    tech_item = next(i for i in precise.items if "技术" in i.title or "参数" in i.requirement)
    assert tech_item.content_source == "precise_search"
    assert tech_item.content_target.get("query")
