from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ContentMode = Literal["full_text", "description"]
Importance = Literal["high", "medium", "low"]


class ConfigCreate(BaseModel):
    title: str = Field(..., max_length=200)
    technique: str = ""
    content_mode: ContentMode
    content_scope: Optional[str] = Field(None, max_length=64)
    content_text: Optional[str] = None
    importance: Importance = "medium"


class ConfigUpdate(BaseModel):
    title: str = Field(..., max_length=200)
    technique: str = ""
    content_mode: ContentMode
    content_scope: Optional[str] = Field(None, max_length=64)
    content_text: Optional[str] = None
    importance: Importance = "medium"


class ConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    technique: str
    content_mode: str
    content_scope: Optional[str]
    content_text: Optional[str]
    importance: str
    created_at: datetime
    updated_at: datetime
