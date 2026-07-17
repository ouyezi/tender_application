from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class _Candidate:
    title: str
    requirement: str
    section: str
    start: int
    end: int
    segment_index: int
    classification_text: str
    coordinate_space: str = "segment"
    synthetic: bool = False


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
        candidates: list[_Candidate] = []
        seen_candidates: set[tuple[str, str]] = set()

        for segment_index, call in enumerate(context.calls):
            self.prompt_prefixes.append(call.stable_prefix)
            for candidate in self._extract_candidates(
                call.tender_segment,
                segment_index,
            ):
                deduplication_key = (
                    self._normalize(candidate.title),
                    self._normalize(candidate.requirement),
                )
                if deduplication_key in seen_candidates:
                    continue
                seen_candidates.add(deduplication_key)
                candidates.append(candidate)

        if not candidates:
            candidates.append(self._synthetic_candidate(0))

        category_names: list[str] = []
        candidate_categories: list[tuple[_Candidate, str]] = []
        for candidate in candidates:
            category_name = self._category_name(candidate.classification_text)
            candidate_categories.append((candidate, category_name))
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
            self._build_item(candidate, index, category_ids[category_name])
            for index, (candidate, category_name) in enumerate(
                candidate_categories,
                start=1,
            )
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
    def _normalize(value: str) -> str:
        return _TITLE_NORMALIZER.sub("", value).casefold()

    @classmethod
    def _extract_candidates(
        cls,
        segment: str,
        segment_index: int,
    ) -> list[_Candidate]:
        if not segment.strip():
            return [cls._synthetic_candidate(segment_index)]

        heading_matches = list(_HEADING_PATTERN.finditer(segment))
        if not heading_matches:
            return cls._body_candidates(
                segment,
                0,
                len(segment),
                segment_index,
                title=None,
            )

        candidates = cls._body_candidates(
            segment,
            0,
            heading_matches[0].start(),
            segment_index,
            title=None,
        )
        for index, heading_match in enumerate(heading_matches):
            title = heading_match.group(1).strip()
            body_end = (
                heading_matches[index + 1].start()
                if index + 1 < len(heading_matches)
                else len(segment)
            )
            section_candidates = cls._body_candidates(
                segment,
                heading_match.end(),
                body_end,
                segment_index,
                title=title,
            )
            if not section_candidates:
                start = heading_match.start(1)
                end = heading_match.end(1)
                section_candidates.append(
                    _Candidate(
                        title=title,
                        requirement=segment[start:end],
                        section=title,
                        start=start,
                        end=end,
                        segment_index=segment_index,
                        classification_text=title,
                    )
                )
            candidates.extend(section_candidates)
        return candidates

    @staticmethod
    def _body_candidates(
        segment: str,
        body_start: int,
        body_end: int,
        segment_index: int,
        *,
        title: str | None,
    ) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        for sentence_match in _SENTENCE_PATTERN.finditer(
            segment,
            body_start,
            body_end,
        ):
            raw_sentence = sentence_match.group()
            requirement = raw_sentence.strip()
            if not requirement:
                continue
            leading_whitespace = len(raw_sentence) - len(raw_sentence.lstrip())
            start = sentence_match.start() + leading_whitespace
            end = start + len(requirement)
            candidate_title = (
                title
                or requirement.rstrip("。！？!?")[:40]
                or "全文完整性检查"
            )
            candidates.append(
                _Candidate(
                    title=candidate_title,
                    requirement=requirement,
                    section=title or "正文",
                    start=start,
                    end=end,
                    segment_index=segment_index,
                    classification_text=f"{candidate_title} {requirement}",
                )
            )
        return candidates

    @staticmethod
    def _synthetic_candidate(segment_index: int) -> _Candidate:
        return _Candidate(
            title="全文完整性检查",
            requirement="确认招标文件正文完整可用，避免遗漏应响应的要求。",
            section="全文",
            start=0,
            end=1,
            segment_index=segment_index,
            classification_text="",
            coordinate_space="synthetic",
            synthetic=True,
        )

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
    def _infer_content_fields(title: str, requirement: str) -> tuple[str, dict[str, Any]]:
        text = f"{title} {requirement}"
        if "全文" in text and "招标" in text:
            return "full_document", {"file_role": "tender"}
        if "标书全文" in text:
            return "large_segments", {"file_role": "bid"}
        if "授权" in text or "资质" in text:
            tags: list[str] = []
            if "授权" in text:
                tags.append("授权证书")
            if "资质" in text:
                tags.append("资质证明")
            return "collection", {"target_tags": tags or ["资质证明"]}
        query = (requirement or title).strip()
        return "precise_search", {"query": query}

    @classmethod
    def _build_item(
        cls,
        candidate: _Candidate,
        index: int,
        category_id: str,
    ) -> ChecklistItemDraft:
        title = candidate.title
        requirement = candidate.requirement
        content_source, content_target = cls._infer_content_fields(title, requirement)
        source_reference: dict[str, Any] = {
            "section": candidate.section,
            "start": candidate.start,
            "end": candidate.end,
            "segment_index": candidate.segment_index,
            "coordinate_space": candidate.coordinate_space,
        }
        if candidate.synthetic:
            source_reference["synthetic"] = True
        return ChecklistItemDraft(
            id=f"item-{index:03d}",
            category_id=category_id,
            title=title,
            requirement=requirement,
            technique=f"检索并核对与“{title}”对应的投标响应及证明材料。",
            importance="high",
            source_references=[source_reference],
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
            content_source=content_source,
            content_target=content_target,
            sort_order=index,
        )
