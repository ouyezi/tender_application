from __future__ import annotations

import re

import jieba
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeChunk
from app.services.retrieval.persist import load_chunk_text

FTS_TABLE = "knowledge_chunks_fts"

_FTS5_SPECIAL = re.compile(r'([\\"])')


def create_fts_table_sql() -> str:
    return """
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
          chunk_id, task_id, file_id, title, body, tokenize='unicode61'
        );
    """


def tokenize_for_fts(text: str) -> str:
    """Segment Chinese text with jieba for FTS5 unicode61 indexing."""
    if not text:
        return ""
    return " ".join(token for token in jieba.cut(text) if token.strip())


def _escape_fts_token(token: str) -> str:
    return _FTS5_SPECIAL.sub(r"\\\1", token)


def _build_match_query(query: str) -> str:
    tokens = [token for token in jieba.cut(query) if token.strip()]
    if not tokens:
        return ""
    return " ".join(_escape_fts_token(token) for token in tokens)


async def delete_fts_for_file(
    session: AsyncSession,
    task_id: str,
    file_id: str,
) -> None:
    await session.execute(
        text(
            f"DELETE FROM {FTS_TABLE} WHERE task_id = :task_id AND file_id = :file_id"
        ),
        {"task_id": task_id, "file_id": file_id},
    )


async def rebuild_fts_for_file(
    session: AsyncSession,
    task_id: str,
    file_id: str,
) -> None:
    """Replace FTS rows for one workspace file after chunk persistence."""
    await delete_fts_for_file(session, task_id, file_id)

    result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.file_id == file_id,
        )
    )
    for chunk in result.scalars():
        body_text = load_chunk_text(chunk)
        indexed_body = tokenize_for_fts(f"{chunk.title} {body_text}")
        await session.execute(
            text(
                f"""
                INSERT INTO {FTS_TABLE} (chunk_id, task_id, file_id, title, body)
                VALUES (:chunk_id, :task_id, :file_id, :title, :body)
                """
            ),
            {
                "chunk_id": chunk.chunk_id,
                "task_id": task_id,
                "file_id": file_id,
                "title": tokenize_for_fts(chunk.title),
                "body": indexed_body,
            },
        )


async def search_fts(
    session: AsyncSession,
    task_id: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, object]]:
    match_query = _build_match_query(query)
    if not match_query:
        return []

    result = await session.execute(
        text(
            f"""
            SELECT
                chunk_id,
                title,
                snippet({FTS_TABLE}, 4, '', '', ' ... ', 64) AS snippet,
                bm25({FTS_TABLE}) AS score
            FROM {FTS_TABLE}
            WHERE {FTS_TABLE} MATCH :match_query
              AND task_id = :task_id
            ORDER BY score
            LIMIT :limit
            """
        ),
        {"match_query": match_query, "task_id": task_id, "limit": limit},
    )

    hits: list[dict[str, object]] = []
    for row in result.mappings():
        hits.append(
            {
                "chunk_id": row["chunk_id"],
                "title": row["title"] or "",
                "snippet": row["snippet"] or "",
                "score": float(row["score"]),
            }
        )
    return hits
