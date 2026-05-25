# === Phase 1: データベース層 START ===
"""add break minutes to lesson reports

Revision ID: 0002_add_break_minutes
Revises: 0001_initial
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_add_break_minutes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lesson_reports", sa.Column("break_minutes", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("lesson_reports", "break_minutes")
# === Phase 1 END ===
