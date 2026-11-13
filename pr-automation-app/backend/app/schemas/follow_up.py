from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FollowUpCreate(BaseModel):
    journalist_id: UUID
    pitch_id: UUID | None = None
    due_at: datetime | None = None
    note: str = Field(default="", max_length=1000)


class FollowUpUpdate(BaseModel):
    status: Literal["pending", "completed", "cancelled"] | None = None
    due_at: datetime | None = None
    snooze_days: int | None = Field(default=None, ge=1, le=90)
    note: str | None = Field(default=None, max_length=1000)


class FollowUpRead(BaseModel):
    id: UUID
    client_id: UUID
    officer_id: UUID
    journalist_id: UUID
    pitch_id: UUID | None
    journalist_name: str
    journalist_outlet: str
    journalist_email: str | None
    pitch_subject: str | None
    due_at: datetime
    status: str
    note: str
    is_overdue: bool
    completed_at: datetime | None
    created_at: datetime
