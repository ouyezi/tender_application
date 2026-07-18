import pytest

from app.engine.checklist_agent_os import (
    TENDER_CHECKLIST_GENERATOR_APP_NAME,
    AgentOSChecklistAgent,
    ChecklistAgentResponseError,
)
from app.services.checklist_context import (
    SYSTEM_INSTRUCTIONS,
    ChecklistCallInput,
    PromptContext,
)


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
