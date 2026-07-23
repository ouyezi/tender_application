from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class MetaBlock(BaseModel):
    title: str = Field(..., min_length=1)
    subtitle: str = ""
    project_key: str = ""


class OverviewRow(BaseModel):
    label: str
    value: str
    label2: Optional[str] = None
    value2: Optional[str] = None
    colspan: Optional[int] = None


class OverviewBlock(BaseModel):
    rows: list[OverviewRow] = Field(default_factory=list)


class RiskItem(BaseModel):
    level: Literal["high", "mid", "low"]
    title: str
    desc: str


class TaskItem(BaseModel):
    name: str
    owner: str = ""
    deadline: str = ""


class TasksBlock(BaseModel):
    p0: list[TaskItem] = Field(default_factory=list)
    p1: list[TaskItem] = Field(default_factory=list)
    p2: list[TaskItem] = Field(default_factory=list)


class ChecklistSection(BaseModel):
    section: str
    items: list[str] = Field(default_factory=list)
    redline: bool = False


class TimelineRow(BaseModel):
    label: str
    value: str
    note: str = ""


class KeyValueRow(BaseModel):
    label: str
    value: str


class KeyInfoBlock(BaseModel):
    timeline: list[TimelineRow] = Field(default_factory=list)
    qualification: list[KeyValueRow] = Field(default_factory=list)
    commercial: list[KeyValueRow] = Field(default_factory=list)
    technical: list[KeyValueRow] = Field(default_factory=list)


class StrategyBlock(BaseModel):
    advantage: str = ""
    risk_avoid: str = ""
    price: str = ""


class ScoringRow(BaseModel):
    dimension: str
    score: str
    weight: str = ""
    criteria: str = ""
    strategy: str = ""


class InterpretHtmlReportData(BaseModel):
    schema_version: Literal["1"]
    meta: MetaBlock
    overview: OverviewBlock
    risks: list[RiskItem] = Field(default_factory=list)
    tasks: TasksBlock = Field(default_factory=TasksBlock)
    checklist: list[ChecklistSection] = Field(default_factory=list)
    key_info: KeyInfoBlock = Field(default_factory=KeyInfoBlock)
    strategy: StrategyBlock = Field(default_factory=StrategyBlock)
    scoring: list[ScoringRow] = Field(default_factory=list)
