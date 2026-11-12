"""Initial multi-tenant PR Automation schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("officer_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=120), nullable=False),
        sa.Column("brand_voice", sa.Text(), nullable=False),
        sa.Column("key_facts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["officer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_clients_officer_id", "clients", ["officer_id"], unique=False)

    op.create_table(
        "journalists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("canonical_name", sa.String(length=200), nullable=False),
        sa.Column("outlet", sa.String(length=200), nullable=False),
        sa.Column("beat", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_status", sa.String(length=32), nullable=False),
        sa.Column("email_source_url", sa.Text(), nullable=True),
        sa.Column("email_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("profile_source_url", sa.Text(), nullable=True),
        sa.Column("profile_scraped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_journalists_beat", "journalists", ["beat"], unique=False)
    op.create_index("ix_journalists_email", "journalists", ["email"], unique=True)
    op.create_index("ix_journalists_outlet", "journalists", ["outlet"], unique=False)

    op.create_table(
        "journalist_socials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("journalist_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("handle_or_url", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journalist_id"], ["journalists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("journalist_id", "platform", "handle_or_url", name="uq_journalist_social"),
    )
    op.create_index("ix_journalist_socials_journalist_id", "journalist_socials", ["journalist_id"], unique=False)

    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("journalist_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("outlet", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["journalist_id"], ["journalists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("url"),
    )
    op.create_index("ix_articles_journalist_id", "articles", ["journalist_id"], unique=False)

    op.create_table(
        "pitches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("officer_id", sa.Uuid(), nullable=False),
        sa.Column("journalist_id", sa.Uuid(), nullable=False),
        sa.Column("cited_article_id", sa.Uuid(), nullable=False),
        sa.Column("template_id", sa.Uuid(), nullable=True),
        sa.Column("tone", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("grounding_claims", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["cited_article_id"], ["articles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["journalist_id"], ["journalists.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["officer_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pitches_client_id", "pitches", ["client_id"], unique=False)
    op.create_index("ix_pitches_client_status", "pitches", ["client_id", "status"], unique=False)
    op.create_index("ix_pitches_journalist_id", "pitches", ["journalist_id"], unique=False)
    op.create_index("ix_pitches_officer_id", "pitches", ["officer_id"], unique=False)
    op.create_index("ix_pitches_status", "pitches", ["status"], unique=False)

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("rfc_message_id", sa.String(length=255), nullable=True),
        sa.Column("in_reply_to", sa.String(length=255), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_headers", sa.JSON(), nullable=False),
        sa.Column("body_snippet", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_messages_in_reply_to", "email_messages", ["in_reply_to"], unique=False)
    op.create_index("ix_email_messages_pitch_id", "email_messages", ["pitch_id"], unique=False)
    op.create_index("ix_email_messages_provider_message_id", "email_messages", ["provider_message_id"], unique=False)
    op.create_index("ix_email_messages_rfc_message_id", "email_messages", ["rfc_message_id"], unique=False)
    op.create_index("ix_email_messages_thread_id", "email_messages", ["thread_id"], unique=False)

    op.create_table(
        "suppressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("journalist_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["journalist_id"], ["journalists.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_suppressions_email", "suppressions", ["email"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"], unique=False)
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("suppressions")
    op.drop_table("email_messages")
    op.drop_table("pitches")
    op.drop_table("articles")
    op.drop_table("journalist_socials")
    op.drop_table("journalists")
    op.drop_table("clients")
    op.drop_table("users")

