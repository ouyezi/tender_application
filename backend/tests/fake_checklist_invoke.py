from __future__ import annotations

import re
from typing import Any

_HEADING_PATTERN = re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+(.+?)\s*$")
_SENTENCE_PATTERN = re.compile(r"[^。\n！？!?]+[。！？!?]?")
_CATEGORY_DEFINITIONS = {
    "资格证明材料": "cat_002",
    "商务评分材料": "cat_004",
    "技术响应材料": "cat_003",
    "综合响应材料": "cat_006",
}


def _sentence_bounds(segment: str, match: re.Match[str]) -> tuple[str, int, int]:
    raw_sentence = match.group()
    requirement = raw_sentence.strip()
    leading_whitespace = len(raw_sentence) - len(raw_sentence.lstrip())
    start = match.start() + leading_whitespace
    end = start + len(requirement)
    return requirement, start, end


def _category_name(text: str) -> str:
    normalized = re.sub(r"\s+", "", text).casefold()
    if any(keyword in normalized for keyword in ("资格", "资质", "证照", "营业执照")):
        return "资格证明材料"
    if any(keyword in normalized for keyword in ("商务", "业绩", "评分", "得分")):
        return "商务评分材料"
    if any(keyword in normalized for keyword in ("技术", "方案", "参数")):
        return "技术响应材料"
    return "综合响应材料"


def _items_for_segment(segment: str, *, segment_index: int) -> list[dict[str, Any]]:
    del segment_index
    items: list[dict[str, Any]] = []
    heading_matches = list(_HEADING_PATTERN.finditer(segment))
    sections: list[tuple[str | None, int, int]] = []
    if heading_matches:
        sections.append((None, 0, heading_matches[0].start()))
        for index, heading_match in enumerate(heading_matches):
            title = heading_match.group(1).strip()
            body_end = (
                heading_matches[index + 1].start()
                if index + 1 < len(heading_matches)
                else len(segment)
            )
            sections.append((title, heading_match.end(), body_end))
    else:
        sections.append((None, 0, len(segment)))

    for title, body_start, body_end in sections:
        classification_text = title or segment[body_start:body_end].strip() or "综合响应"
        category_name = _category_name(classification_text)
        for match in _SENTENCE_PATTERN.finditer(segment, body_start, body_end):
            requirement, _start, _end = _sentence_bounds(segment, match)
            if not requirement:
                continue
            item_title = (title or requirement[:40]).rstrip("。")
            offline_markers = ("装订", "打印", "密封", "盖章")
            probe = f"{item_title}{requirement}"
            diagnosis_mode = (
                "offline"
                if any(marker in probe for marker in offline_markers)
                else "file"
            )
            section = title or "正文"
            items.append(
                {
                    "id": f"item-local-{len(items) + 1}",
                    "category_id": _CATEGORY_DEFINITIONS[category_name],
                    "title": item_title,
                    "requirement": requirement,
                    "technique": "对照招标要求核验材料",
                    "importance": "medium",
                    "source_citations": f"- 章节：{section}",
                    "retrieval_hints": [item_title],
                    "expected_evidence": f"- {item_title}",
                    "compliance_rules": (
                        "## 满足\n材料完整\n\n"
                        "## 违反\n与要求冲突\n\n"
                        "## 不能满足\n无法满足\n\n"
                        "## 证据不足\n证据不足"
                    ),
                    "consequence_rules": "[general_risk]\n存在合规风险",
                    "admin_config_refs": [],
                    "sort_order": len(items) + 1,
                    "diagnosis_mode": diagnosis_mode,
                }
            )

    if not items:
        items.append(
            {
                "id": "item-local-1",
                "category_id": "cat_006",
                "title": "综合响应",
                "requirement": "按招标文件要求提交完整响应材料。",
                "technique": "对照招标要求核验材料",
                "importance": "medium",
                "source_citations": "- 章节：正文",
                "retrieval_hints": ["响应材料"],
                "expected_evidence": "- 完整响应材料",
                "compliance_rules": (
                    "## 满足\n材料完整\n\n"
                    "## 违反\n与要求冲突\n\n"
                    "## 不能满足\n无法满足\n\n"
                    "## 证据不足\n证据不足"
                ),
                "consequence_rules": "[general_risk]\n存在合规风险",
                "admin_config_refs": [],
                "sort_order": 1,
                "diagnosis_mode": "file",
            }
        )
    return items


def build_checklist_payload_for_segment(
    segment: str,
    *,
    segment_index: int,
) -> dict[str, Any]:
    return {
        "schema_version": "2",
        "items": _items_for_segment(segment, segment_index=segment_index),
    }


def make_fake_checklist_invoke(segment_index_by_text: dict[str, int] | None = None):
    del segment_index_by_text
    call_state = {"index": 0}

    async def fake_invoke(app_name: str, input_data: dict[str, object]) -> dict[str, object]:
        del app_name
        segment = str(input_data["tender_segment"])
        segment_index = call_state["index"]
        call_state["index"] += 1
        return build_checklist_payload_for_segment(segment, segment_index=segment_index)

    return fake_invoke
