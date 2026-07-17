from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ContentSource = Literal[
    "full_document", "collection", "large_segments", "precise_search"
]
SegmentLevel = Literal["fine", "large"]
ChunkSource = Literal["native_text", "ocr", "table"]


@dataclass
class SegmentDraft:
    chunk_id: str
    node_id: str
    parent_node_id: str | None
    ancestor_node_ids: list[str]
    segment_level: SegmentLevel
    title_path: list[str]
    start: int
    end: int
    text: str
    child_chunk_ids: list[str] = field(default_factory=list)
    source: ChunkSource = "native_text"
    title: str = ""
    summary: str = ""
    description: str = ""
    tags: list[dict[str, Any]] = field(default_factory=list)  # {name, confidence}
