# === Phase 1: データベース層 START ===
"""allow_null_parent_in_assignments

Revision ID: 0004_allow_null_parent
Revises: 0003_add_tutor_no
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_allow_null_parent"
down_revision = "0003_add_tutor_no"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("assignments", "parent_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column("lesson_reports", "parent_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.alter_column("lesson_reports", "parent_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column("assignments", "parent_id", existing_type=sa.Uuid(), nullable=False)
# === Phase 1 END ===
