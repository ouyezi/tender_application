import pytest

from app.engine.base import RetrievedChunk
from app.engine.batch_diagnosis_agent_os import (
    AgentOSBatchDiagnosisEngine,
    BatchDiagnosisResponseError,
    parse_batch_diagnosis_payload,
)


def test_parse_batch_diagnosis_payload_ok():
    draft = parse_batch_diagnosis_payload(
        {
            "schema_version": "1",
            "results": [
                {
                    "checklist_item_id": "i1",
                    "compliance": "satisfied",
                    "consequence_tags": ["score_risk"],
                    "evidence": "见第3页营业执照",
                    "suggestion": "保持现有材料",
                    "response_content": "已提供有效营业执照复印件",
                    "description": "执照齐全",
                }
            ],
        }
    )
    assert len(draft) == 1
    assert draft[0].checklist_item_id == "i1"
    assert draft[0].compliance == "satisfied"
    assert draft[0].response_content == "已提供有效营业执照复印件"


def test_parse_rejects_bad_compliance():
    with pytest.raises(BatchDiagnosisResponseError, match="compliance"):
        parse_batch_diagnosis_payload(
            {
                "schema_version": "1",
                "results": [
                    {
                        "checklist_item_id": "i1",
                        "compliance": "maybe",
                        "consequence_tags": [],
                        "evidence": "e",
                        "suggestion": "s",
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_diagnose_category_invokes_app():
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        async def invoke_app(self, app_name: str, input_data: dict):
            calls.append((app_name, input_data))
            return {
                "schema_version": "1",
                "results": [
                    {
                        "checklist_item_id": "i1",
                        "compliance": "insufficient_evidence",
                        "consequence_tags": [],
                        "evidence": "检索块不足",
                        "suggestion": "补充材料",
                        "response_content": "标书未找到相关响应",
                        "description": "无法判定",
                    }
                ],
            }

    engine = AgentOSBatchDiagnosisEngine(client=FakeClient())
    results = await engine.diagnose_category(
        task_id="T-1",
        category={"id": "c1", "name": "资格", "description": ""},
        items=[
            {
                "id": "i1",
                "title": "执照",
                "requirement": "提供执照",
                "technique": "查附件",
                "importance": "high",
                "compliance_rules": {
                    "satisfied": "有执照",
                    "violated": "无执照",
                    "cannot_satisfy": "无法提供",
                    "insufficient_evidence": "看不清",
                },
                "consequence_rules": {"bid_unusable": "废标"},
                "expected_evidence": "营业执照扫描件",
            }
        ],
        retrieved_chunks=[
            RetrievedChunk(
                chunk_id="ch1",
                text="执照复印件",
                location="p1",
                document_role="bid",
            )
        ],
    )
    assert calls[0][0] == "tender_batch_diagnosis_app"
    assert "system_instructions" in calls[0][1]
    assert "category_payload" in calls[0][1]
    assert "retrieved_chunks" in calls[0][1]
    chunks_json = calls[0][1]["retrieved_chunks"]
    assert '"document_role": "bid"' in chunks_json
    assert results[0].compliance == "insufficient_evidence"
