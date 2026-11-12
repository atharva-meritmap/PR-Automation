from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class JournalistSocialRead(BaseModel):
    id: UUID
    platform: str
    handle_or_url: str
    source_url: str
    scraped_at: datetime

    model_config = {"from_attributes": True}


class ArticleRead(BaseModel):
    id: UUID
    title: str
    url: str
    published_at: datetime | None
    summary: str
    outlet: str
    source_url: str
    scraped_at: datetime

    model_config = {"from_attributes": True}


class JournalistRead(BaseModel):
    id: UUID
    canonical_name: str
    outlet: str
    beat: str
    email: str | None
    email_status: str
    email_source_url: str | None
    email_scraped_at: datetime | None
    profile_source_url: str | None
    profile_scraped_at: datetime | None
    confidence_score: float
    last_contacted_at: datetime | None
    socials: list[JournalistSocialRead] = []
    articles: list[ArticleRead] = []

    model_config = {"from_attributes": True}


class JournalistScrapeRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    outlet: str = Field(default="", max_length=200)
    beat: str = Field(default="", max_length=200)
    rss_url: str | None = Field(default=None, max_length=2048)
    verify_email: bool = False


class EmailVerificationResponse(BaseModel):
    journalist_id: UUID
    email: str | None
    email_status: str
    mx_host: str | None
    detail: str


class RelevanceScoreResponse(BaseModel):
    journalist_id: UUID
    client_id: UUID
    article_id: UUID | None
    is_relevant: bool
    confidence: float

