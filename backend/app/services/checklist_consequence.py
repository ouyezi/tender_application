from __future__ import annotations

import re

_VALID_TAGS = frozenset({"bid_unusable", "score_risk", "no_score", "general_risk"})
_TAG_LINE = re.compile(r"^\[([^\]]+)\]")


def parse_consequence_tags_from_markdown(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return []
    first_line = text.strip().splitlines()[0].strip()
    match = _TAG_LINE.match(first_line)
    if not match:
        return []
    raw = match.group(1)
    tags: list[str] = []
    for part in raw.split(","):
        tag = part.strip()
        if tag in _VALID_TAGS and tag not in tags:
            tags.append(tag)
    return tags
