from __future__ import annotations

import numpy as np
import pytest

pytest_plugins = ["test_index_scheduler"]


def test_hash_embedding_retrieves_similar_chunk(tmp_path):
    from app.services.retrieval.vectors import HashEmbeddingModel, VectorIndex

    model = HashEmbeddingModel(dim=64)
    index = VectorIndex(tmp_path / "T-1")
    index.upsert(
        [
            ("c1", model.embed("售后服务七天无理由退货")),
            ("c2", model.embed("投标报价汇总表")),
        ]
    )
    q = model.embed("是否支持7天无理由")
    hits = index.search(q, top_k=1)
    assert hits[0][0] == "c1"


def test_hash_embedding_model_embed_many():
    from app.services.retrieval.vectors import HashEmbeddingModel

    model = HashEmbeddingModel(dim=32)
    vectors = model.embed_many(["售后服务", "投标报价"])
    assert len(vectors) == 2
    assert all(isinstance(v, np.ndarray) for v in vectors)
    assert all(v.shape == (32,) for v in vectors)


def test_vector_index_persists_and_reloads(tmp_path):
    from app.services.retrieval.vectors import HashEmbeddingModel, VectorIndex

    model = HashEmbeddingModel(dim=16)
    index_path = tmp_path / "vectors" / "f-1"
    index = VectorIndex(index_path)
    index.upsert([("c1", model.embed("alpha beta")), ("c2", model.embed("gamma delta"))])

    reloaded = VectorIndex(index_path)
    hits = reloaded.search(model.embed("alpha"), top_k=1)
    assert hits[0][0] == "c1"


@pytest.mark.asyncio
async def test_index_scheduler_marks_fine_chunks_embedding_ready(
    db_session, sample_parsed_workspace_file, monkeypatch
):
    from sqlalchemy import select

    from app.config import UPLOAD_DIR
    from app.models import KnowledgeChunk
    from app.services import index_scheduler

    await index_scheduler.enqueue(
        sample_parsed_workspace_file.task_id,
        sample_parsed_workspace_file.id,
    )
    await index_scheduler.drain_once_for_tests()

    fine_chunks = (
        await db_session.execute(
            select(KnowledgeChunk).where(
                KnowledgeChunk.file_id == sample_parsed_workspace_file.id,
                KnowledgeChunk.segment_level == "fine",
            )
        )
    ).scalars().all()
    assert fine_chunks
    assert all(c.embedding_status == "ready" for c in fine_chunks)

    vector_path = (
        UPLOAD_DIR
        / sample_parsed_workspace_file.task_id
        / "vectors"
        / f"{sample_parsed_workspace_file.id}.npz"
    )
    assert vector_path.is_file()
