import pytest

from app.engine.batch_diagnosis_mock import MockBatchDiagnosisEngine
from app.engine.retrieval_mock import MockRetrievalProvider


@pytest.mark.asyncio
async def test_mock_retrieval_returns_chunks():
    provider = MockRetrievalProvider()
    chunks = await provider.retrieve_for_category(
        task_id="T-1",
        category={"id": 1, "name": "资格", "retrieval_query": "资质"},
        items=[{"id": 10, "title": "一级资质", "retrieval_hints": ["资质证书"]}],
    )
    assert len(chunks) >= 1


@pytest.mark.asyncio
async def test_batch_returns_exact_item_ids():
    engine = MockBatchDiagnosisEngine(delay_seconds=0)
    items = [
        {"id": 1, "title": "A", "requirement": "r", "importance": "high"},
        {"id": 2, "title": "B", "requirement": "r", "importance": "low"},
    ]
    results = await engine.diagnose_category(
        task_id="T-1",
        category={"id": 1, "name": "资格"},
        items=items,
        retrieved_chunks=[],
    )
    assert {r.checklist_item_id for r in results} == {1, 2}
    assert all(
        r.compliance
        in {"satisfied", "violated", "cannot_satisfy", "insufficient_evidence"}
        for r in results
    )


@pytest.mark.asyncio
async def test_batch_rejects_incomplete_mapping():
    class BadEngine(MockBatchDiagnosisEngine):
        async def diagnose_category(self, **kwargs):
            results = await super().diagnose_category(**kwargs)
            return results[:-1]  # drop one

    with pytest.raises(ValueError, match="mapping"):
        engine = BadEngine(delay_seconds=0)
        items = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
        results = await engine.diagnose_category(
            task_id="T-1",
            category={"id": 1, "name": "x"},
            items=items,
            retrieved_chunks=[],
        )
        from app.services.checklist_service import assert_batch_complete

        assert_batch_complete(items, results)
