"""add stale and close columns to reports

Revision ID: 0012_stale_close_reports
Revises: 0011_assignment_settings
Create Date: 2026-06-01
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_stale_close_reports"
down_revision = "0011_assignment_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lesson_reports", sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lesson_reports", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lesson_reports", sa.Column("closed_by", sa.Uuid(), nullable=True))
    op.add_column("lesson_reports", sa.Column("close_reason", sa.String(length=500), nullable=True))
    op.create_index(op.f("ix_lesson_reports_closed_by"), "lesson_reports", ["closed_by"], unique=False)
    op.create_foreign_key("fk_lesson_reports_closed_by_users", "lesson_reports", "users", ["closed_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_lesson_reports_closed_by_users", "lesson_reports", type_="foreignkey")
    op.drop_index(op.f("ix_lesson_reports_closed_by"), table_name="lesson_reports")
    op.drop_column("lesson_reports", "close_reason")
    op.drop_column("lesson_reports", "closed_by")
    op.drop_column("lesson_reports", "closed_at")
    op.drop_column("lesson_reports", "stale_since")
