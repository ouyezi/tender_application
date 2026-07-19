from __future__ import annotations

import asyncio

from app.engine.base import InterpretationResult


class MockInterpretationAgent:
    def __init__(self, delay_seconds: float = 0.5) -> None:
        self.delay_seconds = delay_seconds

    async def interpret(
        self,
        *,
        task_id: str,
        tender_text: str,
        background: str,
        requirements: str,
    ) -> InterpretationResult:
        await asyncio.sleep(self.delay_seconds)
        bg = background.strip() or "（未提供项目背景）"
        req = requirements.strip() or "（未提供解读要求）"
        excerpt = tender_text.strip()[:80] or "（空正文）"
        markdown = f"""# 招标文件解读报告

**任务编号：** {task_id}

## 项目概况

基于招标正文「{excerpt}」与项目背景「{bg}」整理如下要点（Mock）。

解读要求：{req}

## 招标范围与资质要求

- 投标人须具备相应资质与业绩
- 联合体投标要求以招标文件为准

## 评分办法要点

- 技术分与商务分权重以招标文件为准
- 响应性检查为否决项前置条件

## 废标/否决条款摘要

- 未按要求密封、签字盖章
- 实质性偏离招标文件要求

## 风险提示

- 请核对保证金递交方式与截止时间
- 请逐条响应废标条款，避免形式废标
"""
        return InterpretationResult(markdown=markdown.strip() + "\n")
