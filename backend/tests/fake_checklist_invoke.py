from __future__ import annotations

import re
from typing import Any

_HEADING_PATTERN = re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+(.+?)\s*$")
_SENTENCE_PATTERN = re.compile(r"[^。\n！？!?]+[。！？!?]?")
_CATEGORY_DEFINITIONS = {
    "资格证明材料": {
        "description": "核验投标主体资格、证照及资质证明材料。",
        "retrieval_query": "资格 证照 营业执照 资质",
        "expected_locations": ["资格审查", "资格证明", "投标人资质"],
    },
    "商务评分材料": {
        "description": "核验商务评分、业绩及得分证明材料。",
        "retrieval_query": "评分 业绩 得分 商务",
        "expected_locations": ["评分办法", "商务部分", "业绩证明"],
    },
    "技术响应材料": {
        "description": "核验技术方案、技术参数及响应材料。",
        "retrieval_query": "技术 方案 参数 响应",
        "expected_locations": ["技术部分", "技术方案", "参数响应"],
    },
    "综合响应材料": {
        "description": "核验未归入专项类别的综合投标响应材料。",
        "retrieval_query": "投标要求 综合响应",
        "expected_locations": ["投标文件", "综合部分"],
    },
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
            requirement, start, end = _sentence_bounds(segment, match)
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
            items.append(
                {
                    "id": f"item-local-{len(items) + 1}",
                    "category_id": f"category-local-{category_name}",
                    "title": item_title,
                    "requirement": requirement,
                    "technique": "对照招标要求核验材料",
                    "importance": "medium",
                    "source_references": [
                        {
                            "section": title or "正文",
                            "start": start,
                            "end": end,
                            "segment_index": segment_index,
                            "coordinate_space": "segment",
                        }
                    ],
                    "retrieval_hints": [item_title],
                    "expected_evidence": [item_title],
                    "compliance_rules": {
                        "satisfied": "材料完整",
                        "violated": "与要求冲突",
                        "cannot_satisfy": "无法满足",
                        "insufficient_evidence": "证据不足",
                    },
                    "consequence_rules": {"general_risk": "存在合规风险"},
                    "admin_config_refs": [],
                    "sort_order": len(items) + 1,
                    "diagnosis_mode": diagnosis_mode,
                    "_category_name": category_name,
                }
            )

    if not items:
        category_name = "综合响应材料"
        items.append(
            {
                "id": "item-local-1",
                "category_id": f"category-local-{category_name}",
                "title": "综合响应",
                "requirement": "按招标文件要求提交完整响应材料。",
                "technique": "对照招标要求核验材料",
                "importance": "medium",
                "source_references": [
                    {
                        "section": "正文",
                        "start": 0,
                        "end": 1,
                        "segment_index": segment_index,
                        "coordinate_space": "segment",
                    }
                ],
                "retrieval_hints": ["响应材料"],
                "expected_evidence": ["完整响应材料"],
                "compliance_rules": {
                    "satisfied": "材料完整",
                    "violated": "与要求冲突",
                    "cannot_satisfy": "无法满足",
                    "insufficient_evidence": "证据不足",
                },
                "consequence_rules": {"general_risk": "存在合规风险"},
                "admin_config_refs": [],
                "sort_order": 1,
                "diagnosis_mode": "file",
                "_category_name": category_name,
            }
        )
    return items


def build_checklist_payload_for_segment(
    segment: str,
    *,
    segment_index: int,
) -> dict[str, Any]:
    raw_items = _items_for_segment(segment, segment_index=segment_index)
    category_names: list[str] = []
    for item in raw_items:
        category_name = str(item.pop("_category_name"))
        if category_name not in category_names:
            category_names.append(category_name)

    categories = [
        {
            "id": f"category-local-{name}",
            "name": name,
            "description": _CATEGORY_DEFINITIONS[name]["description"],
            "retrieval_query": _CATEGORY_DEFINITIONS[name]["retrieval_query"],
            "expected_locations": list(_CATEGORY_DEFINITIONS[name]["expected_locations"]),
            "sort_order": index,
        }
        for index, name in enumerate(category_names, start=1)
    ]
    return {
        "schema_version": "1",
        "categories": categories,
        "items": raw_items,
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
