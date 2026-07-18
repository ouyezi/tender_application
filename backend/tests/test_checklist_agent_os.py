import pytest

from app.engine.checklist_agent_os import (
    TENDER_CHECKLIST_GENERATOR_APP_NAME,
    AgentOSChecklistAgent,
    ChecklistAgentResponseError,
    parse_checklist_payload,
)
from app.services.checklist_context import (
    SYSTEM_INSTRUCTIONS,
    ChecklistCallInput,
    PromptContext,
)
from app.services.checklist_service import validate_draft


def _flash_sparse_payload() -> dict:
    """Shape observed from qwen3.6-flash on real tender segments."""
    return {
        "schema_version": "1",
        "categories": [
            {
                "id": "cat_qualification",
                "name": "资格要求响应",
            }
        ],
        "items": [
            {
                "category_id": "cat_qualification",
                "title": "具备独立法人资格",
                "requirement": "投标人须为独立法人",
                "technique": "对照",
                "importance": "high",
                "compliance_rules": {
                    "satisfied": "有营业执照",
                    "violated": "分支机构投标",
                    "cannot_satisfy": "证件模糊",
                    "insufficient_evidence": "仅有法人身份证",
                },
                "consequence_rules": {"bid_unusable": True},
                "source_references": [
                    {
                        "coordinate_space": "segment",
                        "segment_index": 0,
                        "start": 0,
                        "end": 8,
                        "section": "第一章 资格要求",
                    }
                ],
            }
        ],
    }


def _context(segment: str) -> PromptContext:
    call = ChecklistCallInput(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report="解读",
        admin_config="[]",
        tender_segment=segment,
        segment_index=0,
    )
    return PromptContext(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report="解读",
        admin_config="[]",
        segments=[segment],
        calls=[call],
    )


@pytest.mark.asyncio
async def test_generate_maps_explicit_fields_and_app_name():
    captured = {}

    async def fake_invoke(app_name, input_data):
        captured["app_name"] = app_name
        captured["input"] = input_data
        return {
            "schema_version": "1",
            "categories": [
                {
                    "id": "c1",
                    "name": "资格证明材料",
                    "description": "资格",
                    "retrieval_query": "资格",
                    "expected_locations": ["资格"],
                    "sort_order": 1,
                }
            ],
            "items": [
                {
                    "id": "i1",
                    "category_id": "c1",
                    "title": "营业执照",
                    "requirement": "须提供营业执照",
                    "technique": "核对证照",
                    "importance": "high",
                    "source_references": [
                        {
                            "section": "资格",
                            "start": 0,
                            "end": 1,
                            "segment_index": 0,
                            "coordinate_space": "segment",
                        }
                    ],
                    "retrieval_hints": ["营业执照"],
                    "expected_evidence": ["营业执照复印件"],
                    "compliance_rules": {
                        "satisfied": "有",
                        "violated": "冲突",
                        "cannot_satisfy": "不能",
                        "insufficient_evidence": "不足",
                    },
                    "consequence_rules": {"general_risk": "风险"},
                    "admin_config_refs": [],
                    "sort_order": 1,
                }
            ],
        }

    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    draft = await agent.generate(task_id="T1", context=_context("投标人须提供营业执照。"))
    assert captured["app_name"] == TENDER_CHECKLIST_GENERATOR_APP_NAME
    assert set(captured["input"]) == {
        "system_instructions",
        "interpret_report",
        "admin_config",
        "tender_segment",
    }
    assert captured["input"]["tender_segment"] == "投标人须提供营业执照。"
    assert draft.schema_version == "1"
    assert draft.categories[0].id == "category-001"
    assert draft.items[0].id == "item-001"
    assert agent.agent_type == "agent_os"
    assert agent.agent_version == "1"


@pytest.mark.asyncio
async def test_generate_rejects_missing_categories():
    async def fake_invoke(app_name, input_data):
        return {"schema_version": "1", "items": []}

    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    with pytest.raises(ChecklistAgentResponseError):
        await agent.generate(task_id="T1", context=_context("正文"))


@pytest.mark.asyncio
async def test_generate_invokes_once_per_segment():
    calls = {"n": 0}

    async def fake_invoke(app_name, input_data):
        calls["n"] += 1
        seg_index = 0 if "甲" in input_data["tender_segment"] else 1
        return {
            "schema_version": "1",
            "categories": [
                {
                    "id": "c1",
                    "name": f"分类{seg_index}",
                    "description": "d",
                    "retrieval_query": "q",
                    "expected_locations": [],
                    "sort_order": 1,
                }
            ],
            "items": [
                {
                    "id": "i1",
                    "category_id": "c1",
                    "title": f"标题{seg_index}",
                    "requirement": f"要求{seg_index}",
                    "technique": "t",
                    "importance": "medium",
                    "source_references": [
                        {
                            "section": "s",
                            "start": 0,
                            "end": 1,
                            "segment_index": seg_index,
                            "coordinate_space": "segment",
                        }
                    ],
                    "retrieval_hints": ["h"],
                    "expected_evidence": ["e"],
                    "compliance_rules": {
                        "satisfied": "a",
                        "violated": "b",
                        "cannot_satisfy": "c",
                        "insufficient_evidence": "d",
                    },
                    "consequence_rules": {"score_risk": "扣分"},
                    "admin_config_refs": [],
                    "sort_order": 1,
                }
            ],
        }

    context = PromptContext(
        system_instructions=SYSTEM_INSTRUCTIONS,
        interpret_report="解读",
        admin_config="[]",
        segments=["甲片", "乙片"],
        calls=[
            ChecklistCallInput(
                SYSTEM_INSTRUCTIONS, "解读", "[]", "甲片", 0
            ),
            ChecklistCallInput(
                SYSTEM_INSTRUCTIONS, "解读", "[]", "乙片", 1
            ),
        ],
    )
    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    draft = await agent.generate(task_id="T2", context=context)
    assert calls["n"] == 2
    assert len(draft.items) == 2


def test_parse_checklist_payload_fills_defaults_for_flash_sparse_output():
    draft = parse_checklist_payload(_flash_sparse_payload())
    assert draft.categories[0].id == "cat_qualification"
    assert draft.categories[0].description == "资格要求响应"
    assert draft.categories[0].retrieval_query == "资格要求响应"
    assert draft.categories[0].expected_locations == []
    assert draft.items[0].id
    assert draft.items[0].retrieval_hints == ["具备独立法人资格"]
    assert draft.items[0].expected_evidence == ["具备独立法人资格"]
    assert draft.items[0].admin_config_refs == []
    assert draft.items[0].consequence_rules["bid_unusable"]
    assert draft.items[0].content_target.get("query") == "具备独立法人资格"
    assert draft.items[0].content_target.get("file_role") == "bid"


def test_parse_checklist_payload_rewrites_blank_or_unknown_category_id():
    payload = _flash_sparse_payload()
    payload["categories"].append({"id": "cat_scoring", "name": "评分响应"})
    payload["items"].append(
        {
            "category_id": "",
            "title": "响应评分标准",
            "requirement": "须按评分办法响应",
            "technique": "对照",
            "importance": "medium",
            "compliance_rules": {
                "satisfied": "已响应",
                "violated": "未响应",
                "cannot_satisfy": "无法响应",
                "insufficient_evidence": "证据不足",
            },
            "consequence_rules": ["score_risk"],
            "source_references": [
                {
                    "coordinate_space": "segment",
                    "segment_index": 0,
                    "start": 0,
                    "end": 4,
                    "section": "评分",
                }
            ],
        }
    )
    payload["items"].append(
        {
            "category_id": "missing_cat",
            "title": "未知分类条目",
            "requirement": "测试未知分类",
            "technique": "对照",
            "importance": "low",
            "compliance_rules": {
                "satisfied": "a",
                "violated": "b",
                "cannot_satisfy": "c",
                "insufficient_evidence": "d",
            },
            "consequence_rules": {"general_risk": "风险"},
            "source_references": [
                {
                    "coordinate_space": "segment",
                    "segment_index": 0,
                    "start": 0,
                    "end": 2,
                    "section": "其他",
                }
            ],
        }
    )
    draft = parse_checklist_payload(payload)
    assert draft.items[1].category_id == "cat_qualification"
    assert draft.items[2].category_id == "cat_qualification"


@pytest.mark.asyncio
async def test_generate_accepts_flash_sparse_payload_and_passes_validation():
    async def fake_invoke(app_name, input_data):
        return _flash_sparse_payload()

    segment = "投标人须具备独立法人资格。"
    context = _context(segment)
    agent = AgentOSChecklistAgent(invoke_app=fake_invoke)
    draft = await agent.generate(task_id="T-flash", context=context)
    validate_draft(
        draft,
        context,
        tender_markdown=segment,
        admin_configs=[],
    )
    assert len(draft.categories) == 1
    assert len(draft.items) == 1
    assert draft.categories[0].description
    assert draft.items[0].retrieval_hints
    assert draft.items[0].expected_evidence
