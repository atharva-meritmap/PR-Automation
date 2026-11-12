from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class QwotedConnectRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class QwotedStatusResponse(BaseModel):
    connected: bool
    email: str | None = None


class DiscoverOpportunitiesRequest(BaseModel):
    topic_override: str = ""
    max_results: int = Field(default=10, ge=1, le=30)


class DraftPitchRequest(BaseModel):
    custom_pitch: str | None = None


class SubmitPitchRequest(BaseModel):
    pitch_text: str | None = None
    confirm_live_submit: bool = False


class PrOpportunityRead(BaseModel):
    id: UUID
    client_id: UUID
    source_platform: str
    external_id: str
    title: str
    summary: str
    publication: str
    journalist_name: str
    deadline: str
    share_url: str
    hashtags: list[str]
    is_relevant: bool
    confidence: float
    pitch_status: str
    draft_pitch: str
    scraped_at: datetime

    model_config = {"from_attributes": True}


class PitchSubmitResult(BaseModel):
    opportunity_id: UUID
    pitch_status: str
    mode: str  # dry_run | qwoted_live
    detail: str

