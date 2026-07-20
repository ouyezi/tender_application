from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.engine.base import RetrievalHit


@dataclass
class DebugTrace:
    rewrite: dict[str, Any] = field(default_factory=dict)
    channels: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    merged: list[dict[str, Any]] = field(default_factory=list)
    pre_rerank_order: list[str] = field(default_factory=list)
    post_rerank_order: list[str] = field(default_factory=list)
    ai_rerank: dict[str, Any] = field(default_factory=dict)
    expansions: list[dict[str, str]] = field(default_factory=list)
    context_resolutions: list[dict[str, Any]] = field(default_factory=list)
    skipped_stages: list[str] = field(default_factory=list)


@dataclass
class DebugRetrievalResult:
    mode: str
    items: list[RetrievalHit]
    index_status: str
    incomplete: bool = False
    degraded: bool = False
    error: str | None = None
    path_note: str = ""
    trace: DebugTrace | None = None

    def to_dict(self) -> dict[str, Any]:
        def hit_dict(h: RetrievalHit) -> dict[str, Any]:
            return {
                "chunk_id": h.chunk_id,
                "file_id": h.file_id,
                "node_id": h.node_id,
                "segment_level": h.segment_level,
                "title": h.title,
                "summary": h.summary,
                "title_path": h.title_path,
                "tags": h.tags,
                "text": h.text,
                "child_chunk_ids": h.child_chunk_ids,
                "score": h.score,
                "document_role": h.document_role,
                "context_role": h.context_role,
                "derived_from": h.derived_from,
                "anchor_chunk_id": h.anchor_chunk_id,
            }

        return {
            "mode": self.mode,
            "items": [hit_dict(i) for i in self.items],
            "index_status": self.index_status,
            "incomplete": self.incomplete,
            "degraded": self.degraded,
            "error": self.error,
            "path_note": self.path_note,
            "trace": asdict(self.trace) if self.trace else None,
        }
