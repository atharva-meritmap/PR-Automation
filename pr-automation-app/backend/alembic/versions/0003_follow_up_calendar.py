"""Add follow_ups table for journalist follow-up calendar.

Revision ID: 0003_follow_up_calendar
Revises: 0002_pr_opportunities
Create Date: 2026-11-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_follow_up_calendar"
down_revision: Union[str, None] = "0002_pr_opportunities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "follow_ups",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("officer_id", sa.Uuid(), nullable=False),
        sa.Column("journalist_id", sa.Uuid(), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["officer_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["journalist_id"], ["journalists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_follow_ups_client_id", "follow_ups", ["client_id"])
    op.create_index("ix_follow_ups_officer_id", "follow_ups", ["officer_id"])
    op.create_index("ix_follow_ups_journalist_id", "follow_ups", ["journalist_id"])
    op.create_index("ix_follow_ups_due_at", "follow_ups", ["due_at"])
    op.create_index("ix_follow_ups_status", "follow_ups", ["status"])


def downgrade() -> None:
    op.drop_index("ix_follow_ups_status", table_name="follow_ups")
    op.drop_index("ix_follow_ups_due_at", table_name="follow_ups")
    op.drop_index("ix_follow_ups_journalist_id", table_name="follow_ups")
    op.drop_index("ix_follow_ups_officer_id", table_name="follow_ups")
    op.drop_index("ix_follow_ups_client_id", table_name="follow_ups")
    op.drop_table("follow_ups")

