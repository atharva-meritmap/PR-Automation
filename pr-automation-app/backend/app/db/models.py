import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, TimestampMixin, utc_now


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="officer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    clients: Mapped[list["Client"]] = relationship(
        back_populates="officer",
        foreign_keys="Client.officer_id",
    )


class Client(Base, TimestampMixin):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    officer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    brand_voice: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_facts: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    officer: Mapped["User"] = relationship(back_populates="clients", foreign_keys=[officer_id])
    pitches: Mapped[list["Pitch"]] = relationship(back_populates="client")


class Journalist(Base, TimestampMixin):
    __tablename__ = "journalists"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    canonical_name: Mapped[str] = mapped_column(String(200), nullable=False)
    outlet: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    beat: Mapped[str] = mapped_column(String(200), nullable=False, default="", index=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True, index=True)
    email_status: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    email_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    socials: Mapped[list["JournalistSocial"]] = relationship(
        back_populates="journalist", cascade="all, delete-orphan"
    )
    articles: Mapped[list["Article"]] = relationship(
        back_populates="journalist", cascade="all, delete-orphan"
    )


class JournalistSocial(Base):
    __tablename__ = "journalist_socials"
    __table_args__ = (
        UniqueConstraint("journalist_id", "platform", "handle_or_url", name="uq_journalist_social"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    journalist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journalists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    platform: Mapped[str] = mapped_column(String(40), nullable=False)
    handle_or_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    journalist: Mapped["Journalist"] = relationship(back_populates="socials")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    journalist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journalists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    outlet: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    journalist: Mapped["Journalist"] = relationship(back_populates="articles")


class Pitch(Base, TimestampMixin):
    __tablename__ = "pitches"
    __table_args__ = (
        Index("ix_pitches_client_status", "client_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    officer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    journalist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journalists.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    cited_article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("articles.id", ondelete="RESTRICT"), nullable=False
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    tone: Mapped[str] = mapped_column(String(32), nullable=False, default="conversational")
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    grounding_claims: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="drafted", index=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client: Mapped["Client"] = relationship(back_populates="pitches")


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="dry_run")
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="sandboxed")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_headers: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    body_snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Suppression(Base):
    __tablename__ = "suppressions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    journalist_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("journalists.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(120), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class QwotedConnection(Base, TimestampMixin):
    __tablename__ = "qwoted_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    officer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    qwoted_email: Mapped[str] = mapped_column(String(320), nullable=False)
    cookies_json: Mapped[dict[str, str]] = mapped_column(JSONType, nullable=False, default=dict)


class PrOpportunity(Base, TimestampMixin):
    __tablename__ = "pr_opportunities"
    __table_args__ = (
        UniqueConstraint("client_id", "source_platform", "external_id", name="uq_client_platform_ext"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_platform: Mapped[str] = mapped_column(String(32), nullable=False, default="public_callout")
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    publication: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    journalist_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    deadline: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    share_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    hashtags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    is_relevant: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    pitch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    draft_pitch: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class FollowUp(Base, TimestampMixin):
    __tablename__ = "follow_ups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False
    )
    officer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    journalist_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("journalists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    pitch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="SET NULL"), nullable=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    journalist: Mapped["Journalist"] = relationship()
    pitch: Mapped["Pitch | None"] = relationship()


