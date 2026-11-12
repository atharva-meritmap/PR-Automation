from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(default="", max_length=255)
    industry: str = Field(default="", max_length=120)
    brand_voice: str = ""
    key_facts: dict[str, Any] = Field(default_factory=dict)
    officer_id: UUID | None = None  # Admin may specify; officers are forced to self


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    domain: str | None = Field(default=None, min_length=1, max_length=255)
    industry: str | None = Field(default=None, max_length=120)
    brand_voice: str | None = None
    key_facts: dict[str, Any] | None = None


class ClientReassign(BaseModel):
    officer_id: UUID


class ClientRead(BaseModel):
    id: UUID
    officer_id: UUID
    name: str
    domain: str
    industry: str
    brand_voice: str
    key_facts: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CuratedJournalistMatch(BaseModel):
    journalist_id: UUID
    canonical_name: str
    outlet: str
    beat: str
    email: str | None
    email_status: str
    is_relevant: bool
    confidence: float
    matched_article_title: str | None
    matched_article_url: str | None
    in_cooldown: bool
    suppressed: bool


class BulkDiscoveryRequest(BaseModel):
    topic_override: str = Field(default="", max_length=200)
    max_results: int = Field(default=10, ge=1, le=25)

