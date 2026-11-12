from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class PitchGenerateRequest(BaseModel):
    client_id: UUID
    journalist_id: UUID
    tone: str = Field(default="conversational", max_length=32)


class PitchUpdateRequest(BaseModel):
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    body: str | None = Field(default=None, min_length=1)


class PitchRead(BaseModel):
    id: UUID
    client_id: UUID
    officer_id: UUID
    journalist_id: UUID
    cited_article_id: UUID
    tone: str
    subject: str
    body: str
    grounding_claims: dict[str, Any]
    status: str
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime
    journalist_name: str = ""
    journalist_email: str | None = None
    journalist_email_status: str = "missing"
    journalist_outlet: str = ""
    cited_article_title: str = ""
    cited_article_url: str = ""

    model_config = {"from_attributes": True}


class EmailMessageRead(BaseModel):
    id: UUID
    pitch_id: UUID
    direction: str
    provider: str
    status: str
    sent_at: datetime | None = None
    received_at: datetime | None = None
    body_snippet: str

    model_config = {"from_attributes": True}


class ReplyIngestRequest(BaseModel):
    body: str = Field(min_length=1)


class ReplyClassificationRead(BaseModel):
    pitch_id: UUID
    intent: str
    confidence: float
    suppressed: bool
    pitch_status: str
    detail: str

