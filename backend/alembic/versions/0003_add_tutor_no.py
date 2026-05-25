# === Phase 1: データベース層 START ===
"""add tutor_no

Revision ID: 0003_add_tutor_no
Revises: 0002_add_break_minutes
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_add_tutor_no"
down_revision = "0002_add_break_minutes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("tutor_no", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "tutor_no")
# === Phase 1 END ===
