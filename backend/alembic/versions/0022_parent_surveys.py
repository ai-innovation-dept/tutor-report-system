"""add parent_surveys（保護者アンケート・改修 202607231755 ③）

保護者が講師への満足度・評価を回答するアンケート。指導月報×1件・回答は任意。
- q_satisfaction / q_clarity / q_communication / q_motivation / q_punctuality: 5段階評価（1-5）
- q_continuation: 継続意向（continue / neutral / change）
- comment: 自由記述（任意）
- assignment_id / tutor_id / parent_id / target_month: 集計用の非正規化コピー
閲覧は運営スタッフのみ（講師には一切開示しない）。

Revision ID: 0022_parent_surveys
Revises: 0021_release_deleted_user_emails
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_parent_surveys"
down_revision = "0021_release_deleted_user_emails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parent_surveys",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("monthly_report_id", sa.Uuid(), sa.ForeignKey("monthly_reports.id"), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("assignments.id"), nullable=False),
        sa.Column("tutor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_month", sa.String(length=7), nullable=False),
        sa.Column("q_satisfaction", sa.Integer(), nullable=False),
        sa.Column("q_clarity", sa.Integer(), nullable=False),
        sa.Column("q_communication", sa.Integer(), nullable=False),
        sa.Column("q_motivation", sa.Integer(), nullable=False),
        sa.Column("q_punctuality", sa.Integer(), nullable=False),
        sa.Column("q_continuation", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("monthly_report_id", name="uq_parent_survey_monthly_report"),
    )
    op.create_index("ix_parent_surveys_monthly_report_id", "parent_surveys", ["monthly_report_id"])
    op.create_index("ix_parent_surveys_assignment_id", "parent_surveys", ["assignment_id"])
    op.create_index("ix_parent_surveys_tutor_id", "parent_surveys", ["tutor_id"])
    op.create_index("ix_parent_surveys_parent_id", "parent_surveys", ["parent_id"])
    op.create_index("ix_parent_surveys_target_month", "parent_surveys", ["target_month"])


def downgrade() -> None:
    op.drop_index("ix_parent_surveys_target_month", table_name="parent_surveys")
    op.drop_index("ix_parent_surveys_parent_id", table_name="parent_surveys")
    op.drop_index("ix_parent_surveys_tutor_id", table_name="parent_surveys")
    op.drop_index("ix_parent_surveys_assignment_id", table_name="parent_surveys")
    op.drop_index("ix_parent_surveys_monthly_report_id", table_name="parent_surveys")
    op.drop_table("parent_surveys")
