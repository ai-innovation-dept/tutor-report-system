"""add monthly_reports（指導月報）

講師が月ごとに作成する指導月報（原本: 原本_月報.pdf）。担当×対象月で1件。
- grade: 学年（フリーフォーマット）
- form_data: 問題点と対策・志望校・テスト結果・指導実施日・今月を振り返って・連絡事項（JSON）
- parent_note / parent_note_by / parent_note_at: 保護者記入欄（保護者が承認時に記入。講師は記入不可）

Revision ID: 0019_monthly_reports
Revises: 0018_lesson_grade_fields
Create Date: 2026-07-10
"""

from alembic import op
import sqlalchemy as sa


revision = "0019_monthly_reports"
down_revision = "0018_lesson_grade_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_reports",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("tutor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("target_month", sa.String(length=7), nullable=False),
        sa.Column("grade", sa.String(length=50), nullable=True),
        sa.Column("form_data", sa.JSON(), nullable=False),
        sa.Column("parent_note", sa.Text(), nullable=True),
        sa.Column("parent_note_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("parent_note_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("assignment_id", "target_month", name="uq_monthly_report_assignment_month"),
    )
    op.create_index("ix_monthly_reports_assignment_id", "monthly_reports", ["assignment_id"])
    op.create_index("ix_monthly_reports_tutor_id", "monthly_reports", ["tutor_id"])
    op.create_index("ix_monthly_reports_parent_id", "monthly_reports", ["parent_id"])
    op.create_index("ix_monthly_reports_target_month", "monthly_reports", ["target_month"])


def downgrade() -> None:
    op.drop_index("ix_monthly_reports_target_month", table_name="monthly_reports")
    op.drop_index("ix_monthly_reports_parent_id", table_name="monthly_reports")
    op.drop_index("ix_monthly_reports_tutor_id", table_name="monthly_reports")
    op.drop_index("ix_monthly_reports_assignment_id", table_name="monthly_reports")
    op.drop_table("monthly_reports")
