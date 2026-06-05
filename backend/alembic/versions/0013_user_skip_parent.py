"""add skip_parent_approval to users

Revision ID: 0013_user_skip_parent
Revises: 0012_stale_close_reports
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_user_skip_parent"
down_revision = "0012_stale_close_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("skip_parent_approval", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("users", "skip_parent_approval", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "skip_parent_approval")
