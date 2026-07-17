"""講師の「当月授業なし」申請＋学校の締め日通知設定テーブルを追加

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-17

改修依頼 202607161140:
- work_no_lesson_months: 講師が講師画面で申請する「当月授業なし」（講師×月・全契約対象）。
  申請中の講師は学校の「契約講師全員の学校承認完了」通知の集計対象外になる。
- work_school_settings: 学校ユーザー単位の早期チェックON/OFFと通知日数（締め日の何日前）。
- work_school_deadlines: 学校×対象月ごとの締め日（年間分を月単位で設定）。notice_sent_at は
  締め日前確認メールの送信済みガード（締め日を変更すると None に戻り再送対象になる）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_no_lesson_months",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tutor_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_month", sa.String(length=7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tutor_id", "target_month", name="uq_work_no_lesson_tutor_month"),
    )
    op.create_index("ix_work_no_lesson_months_tutor_id", "work_no_lesson_months", ["tutor_id"])
    op.create_index("ix_work_no_lesson_months_target_month", "work_no_lesson_months", ["target_month"])

    op.create_table(
        "work_school_settings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("school_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("early_check_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notice_days_before", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_work_school_settings_school_id", "work_school_settings", ["school_id"])

    op.create_table(
        "work_school_deadlines",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("school_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_month", sa.String(length=7), nullable=False),
        sa.Column("deadline_date", sa.Date(), nullable=False),
        sa.Column("notice_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("school_id", "target_month", name="uq_work_school_deadline_month"),
    )
    op.create_index("ix_work_school_deadlines_school_id", "work_school_deadlines", ["school_id"])
    op.create_index("ix_work_school_deadlines_target_month", "work_school_deadlines", ["target_month"])


def downgrade() -> None:
    op.drop_index("ix_work_school_deadlines_target_month", table_name="work_school_deadlines")
    op.drop_index("ix_work_school_deadlines_school_id", table_name="work_school_deadlines")
    op.drop_table("work_school_deadlines")
    op.drop_index("ix_work_school_settings_school_id", table_name="work_school_settings")
    op.drop_table("work_school_settings")
    op.drop_index("ix_work_no_lesson_months_target_month", table_name="work_no_lesson_months")
    op.drop_index("ix_work_no_lesson_months_tutor_id", table_name="work_no_lesson_months")
    op.drop_table("work_no_lesson_months")
