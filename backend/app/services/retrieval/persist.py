from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeChunk, WikiPage
from app.services.retrieval.types import SegmentDraft

TEXT_INLINE_LIMIT_BYTES = 8 * 1024


def purge_orphaned_text_files(
    old_paths: list[Path],
    new_paths: set[Path],
) -> None:
    """Delete on-disk text files superseded by a successful re-index."""
    for path in old_paths:
        if path not in new_paths and path.is_file():
            path.unlink()


async def invalidate_file_index(
    session: AsyncSession,
    task_id: str,
    file_id: str,
) -> list[Path]:
    """Remove persisted chunks (and wiki member refs) for one workspace file.

    Returns former ``text_path`` values so callers can delete the files only
    after the replacement index commits successfully.
    """
    result = await session.execute(
        select(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.file_id == file_id,
        )
    )
    chunks = result.scalars().all()
    if not chunks:
        return []

    chunk_ids = {chunk.chunk_id for chunk in chunks}
    old_text_paths = [
        Path(chunk.text_path) for chunk in chunks if chunk.text_path
    ]

    wiki_result = await session.execute(
        select(WikiPage).where(WikiPage.task_id == task_id)
    )
    for page in wiki_result.scalars():
        members = json.loads(page.member_chunk_ids or "[]")
        filtered = [cid for cid in members if cid not in chunk_ids]
        if len(filtered) != len(members):
            page.member_chunk_ids = json.dumps(filtered, ensure_ascii=False)

    await session.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.task_id == task_id,
            KnowledgeChunk.file_id == file_id,
        )
    )
    return old_text_paths


def external_text_paths(
    segments: list[SegmentDraft],
    text_dir: Path,
) -> set[Path]:
    return {
        text_dir / f"{seg.chunk_id}.txt"
        for seg in segments
        if len(seg.text.encode("utf-8")) > TEXT_INLINE_LIMIT_BYTES
    }


async def write_segments(
    session: AsyncSession,
    task_id: str,
    file_id: str,
    segments: list[SegmentDraft],
    text_dir: Path,
) -> None:
    """Persist materialized segments as ``KnowledgeChunk`` rows."""
    text_dir.mkdir(parents=True, exist_ok=True)

    for seg in segments:
        text_path: str | None = None
        text_inline: str | None = None
        encoded = seg.text.encode("utf-8")
        if len(encoded) > TEXT_INLINE_LIMIT_BYTES:
            out_path = text_dir / f"{seg.chunk_id}.txt"
            out_path.write_text(seg.text, encoding="utf-8")
            text_path = str(out_path)
        else:
            text_inline = seg.text

        session.add(
            KnowledgeChunk(
                task_id=task_id,
                file_id=file_id,
                chunk_id=seg.chunk_id,
                node_id=seg.node_id,
                parent_node_id=seg.parent_node_id,
                ancestor_node_ids=json.dumps(seg.ancestor_node_ids, ensure_ascii=False),
                segment_level=seg.segment_level,
                title=seg.title,
                summary=seg.summary,
                description=seg.description,
                tags=json.dumps(seg.tags, ensure_ascii=False),
                title_path=json.dumps(seg.title_path, ensure_ascii=False),
                start=seg.start,
                end=seg.end,
                text_path=text_path,
                text_inline=text_inline,
                child_chunk_ids=json.dumps(seg.child_chunk_ids, ensure_ascii=False),
                source=seg.source,
                index_status="ready",
            )
        )


def load_chunk_text(chunk: KnowledgeChunk) -> str:
    if chunk.text_inline:
        return chunk.text_inline
    if chunk.text_path:
        return Path(chunk.text_path).read_text(encoding="utf-8")
    return ""
