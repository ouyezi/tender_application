from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from app.engine.base import (
    ChecklistCategoryDraft,
    ChecklistDraft,
    ChecklistItemDraft,
)
from app.services.checklist_context import PromptContext


_HEADING_PATTERN = re.compile(r"(?m)^ {0,3}#{1,6}[ \t]+(.+?)\s*$")
_SENTENCE_PATTERN = re.compile(r"[^。\n！？!?]+[。！？!?]?")
_TITLE_NORMALIZER = re.compile(r"[\W_]+", re.UNICODE)

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


class MockChecklistAgent:
    agent_type = "mock"
    agent_version = "1"

    def __init__(self) -> None:
        self.prompt_prefixes: list[str] = []

    async def generate(
        self,
        *,
        task_id: str,
        context: PromptContext,
    ) -> ChecklistDraft:
        del task_id
        self.prompt_prefixes = []
        candidates: list[dict[str, Any]] = []
        seen_titles: set[str] = set()

        for segment_index, call in enumerate(context.calls):
            self.prompt_prefixes.append(call.stable_prefix)
            candidate = self._extract_candidate(call.tender_segment, segment_index)
            normalized_title = _TITLE_NORMALIZER.sub("", candidate["title"]).casefold()
            if normalized_title in seen_titles:
                continue
            seen_titles.add(normalized_title)
            candidates.append(candidate)

        if not candidates:
            candidates.append(self._extract_candidate("", 0))

        category_names: list[str] = []
        for candidate in candidates:
            category_name = self._category_name(candidate["classification_text"])
            candidate["category_name"] = category_name
            if category_name not in category_names:
                category_names.append(category_name)

        category_ids = {
            name: f"category-{index:03d}"
            for index, name in enumerate(category_names, start=1)
        }
        categories = [
            ChecklistCategoryDraft(
                id=category_ids[name],
                name=name,
                description=_CATEGORY_DEFINITIONS[name]["description"],
                retrieval_query=_CATEGORY_DEFINITIONS[name]["retrieval_query"],
                expected_locations=list(
                    _CATEGORY_DEFINITIONS[name]["expected_locations"]
                ),
                sort_order=index,
            )
            for index, name in enumerate(category_names, start=1)
        ]
        items = [
            self._build_item(candidate, index, category_ids[candidate["category_name"]])
            for index, candidate in enumerate(candidates, start=1)
        ]
        raw_response = {
            "schema_version": "1",
            "categories": [asdict(category) for category in categories],
            "items": [asdict(item) for item in items],
        }
        return ChecklistDraft(
            schema_version="1",
            categories=categories,
            items=items,
            raw_response=raw_response,
        )

    @staticmethod
    def _extract_candidate(segment: str, segment_index: int) -> dict[str, Any]:
        heading_match = _HEADING_PATTERN.search(segment)
        if not segment.strip():
            return {
                "title": "全文完整性检查",
                "requirement": "确认招标文件正文完整可用，避免遗漏应响应的要求。",
                "section": "全文",
                "start": 0,
                "end": 1,
                "segment_index": segment_index,
                "classification_text": "",
            }

        title = heading_match.group(1).strip() if heading_match else ""
        body_start = heading_match.end() if heading_match else 0
        body = segment[body_start:]
        sentence_match = next(
            (
                match
                for match in _SENTENCE_PATTERN.finditer(body)
                if match.group().strip()
            ),
            None,
        )
        if sentence_match:
            requirement = sentence_match.group().strip()
            start = body_start + sentence_match.start()
            while start < len(segment) and segment[start].isspace():
                start += 1
            end = start + len(requirement)
        else:
            requirement = title or segment.strip()
            start = heading_match.start(1) if heading_match else segment.index(requirement)
            end = start + len(requirement)

        if not title:
            title = requirement.rstrip("。！？!?")[:40] or "全文完整性检查"
        return {
            "title": title,
            "requirement": requirement,
            "section": title,
            "start": start,
            "end": max(start + 1, end),
            "segment_index": segment_index,
            "classification_text": f"{title} {requirement}",
        }

    @staticmethod
    def _category_name(text: str) -> str:
        if any(keyword in text for keyword in ("资格", "证照", "营业执照", "资质")):
            return "资格证明材料"
        if any(keyword in text for keyword in ("评分", "业绩", "得分")):
            return "商务评分材料"
        if any(keyword in text for keyword in ("技术", "方案", "参数")):
            return "技术响应材料"
        return "综合响应材料"

    @staticmethod
    def _build_item(
        candidate: dict[str, Any],
        index: int,
        category_id: str,
    ) -> ChecklistItemDraft:
        title = candidate["title"]
        requirement = candidate["requirement"]
        return ChecklistItemDraft(
            id=f"item-{index:03d}",
            category_id=category_id,
            title=title,
            requirement=requirement,
            technique=f"检索并核对与“{title}”对应的投标响应及证明材料。",
            importance="high",
            source_references=[
                {
                    "section": candidate["section"],
                    "start": candidate["start"],
                    "end": candidate["end"],
                    "segment_index": candidate["segment_index"],
                }
            ],
            retrieval_hints=[title, requirement],
            expected_evidence=[f"与“{title}”对应的完整响应或证明材料"],
            compliance_rules={
                "satisfied": "已提供完整、有效且与招标要求一致的材料。",
                "violated": "材料内容与招标要求明确冲突。",
                "cannot_satisfy": "投标文件明确表明无法满足该项要求。",
                "insufficient_evidence": "未找到足以判断是否满足要求的材料。",
            },
            consequence_rules={
                "general_risk": "缺失或不符合要求可能导致合规风险。"
            },
            admin_config_refs=[],
            sort_order=index,
        )
