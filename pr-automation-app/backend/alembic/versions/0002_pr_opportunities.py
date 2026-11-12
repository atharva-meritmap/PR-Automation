"""Add qwoted_connections and pr_opportunities tables.

Revision ID: 0002_pr_opportunities
Revises: 0001_initial_schema
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_pr_opportunities"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qwoted_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("officer_id", sa.Uuid(), nullable=False),
        sa.Column("qwoted_email", sa.String(length=320), nullable=False),
        sa.Column("cookies_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["officer_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("officer_id"),
    )
    op.create_index(
        "ix_qwoted_connections_officer_id", "qwoted_connections", ["officer_id"], unique=True
    )

    op.create_table(
        "pr_opportunities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("source_platform", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("publication", sa.String(length=255), nullable=False),
        sa.Column("journalist_name", sa.String(length=255), nullable=False),
        sa.Column("deadline", sa.String(length=120), nullable=False),
        sa.Column("share_url", sa.String(length=1024), nullable=False),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("is_relevant", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("pitch_status", sa.String(length=32), nullable=False),
        sa.Column("draft_pitch", sa.Text(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "source_platform", "external_id", name="uq_client_platform_ext"),
    )
    op.create_index("ix_pr_opportunities_client_id", "pr_opportunities", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_pr_opportunities_client_id", table_name="pr_opportunities")
    op.drop_table("pr_opportunities")
    op.drop_index("ix_qwoted_connections_officer_id", table_name="qwoted_connections")
    op.drop_table("qwoted_connections")

