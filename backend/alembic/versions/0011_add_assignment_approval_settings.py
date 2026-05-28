"""add assignment approval settings

Revision ID: 0011_assignment_settings
Revises: 0010_add_user_deleted_at
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_assignment_settings"
down_revision = "0010_add_user_deleted_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assignments", sa.Column("skip_parent_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("assignments", sa.Column("reminder_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("assignments", sa.Column("reminder_days_after", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("assignments", sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="1"))
    op.alter_column("assignments", "skip_parent_approval", server_default=None)
    op.alter_column("assignments", "reminder_enabled", server_default=None)
    op.alter_column("assignments", "reminder_days_after", server_default=None)
    op.alter_column("assignments", "reminder_count", server_default=None)


def downgrade() -> None:
    op.drop_column("assignments", "reminder_count")
    op.drop_column("assignments", "reminder_days_after")
    op.drop_column("assignments", "reminder_enabled")
    op.drop_column("assignments", "skip_parent_approval")
