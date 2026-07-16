"""add deadline_notice_sends（提出締切メール通知の送信済み記録）

講師向け提出締切通知メール（改修依頼 202607161428）を「月×種別につき1回だけ」送る
ためのガードテーブル。
- target_month: 対象月 YYYY-MM
- notice_type: deadline_first（月中通知）/ deadline_eve（締切前日通知）
- recipient_count: 投函した宛先数（監査用）

Revision ID: 0020_deadline_notice_sends
Revises: 0019_monthly_reports
Create Date: 2026-07-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0020_deadline_notice_sends"
down_revision = "0019_monthly_reports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deadline_notice_sends",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("target_month", sa.String(length=7), nullable=False),
        sa.Column("notice_type", sa.String(length=32), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_month", "notice_type", name="uq_deadline_notice_month_type"),
    )
    op.create_index("ix_deadline_notice_sends_target_month", "deadline_notice_sends", ["target_month"])


def downgrade() -> None:
    op.drop_index("ix_deadline_notice_sends_target_month", table_name="deadline_notice_sends")
    op.drop_table("deadline_notice_sends")
