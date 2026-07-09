"""add lesson report detail fields (教科ほか7項目化)

指導報告の入力項目を「科目＋指導内容」から下記7項目へ再構築するための追加カラム。
- 既存 subject（科目）は「教科」として流用（名称変更のみ・データ引継ぎ）。
- 既存 content（指導内容）は「(b) 何を指導したか/単元など」として流用（データ引継ぎ）。
- 以下6カラムを新設。既存行は NULL（過去データは破棄しない）。

Revision ID: 0017_lesson_detail_fields
Revises: 0016_mail_outbox
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_lesson_detail_fields"
down_revision = "0016_mail_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lesson_reports", sa.Column("material_name", sa.Text(), nullable=True))
    op.add_column("lesson_reports", sa.Column("learning_status", sa.Text(), nullable=True))
    op.add_column("lesson_reports", sa.Column("homework_status", sa.String(length=1), nullable=True))
    op.add_column("lesson_reports", sa.Column("next_homework", sa.Text(), nullable=True))
    op.add_column("lesson_reports", sa.Column("next_lesson_date", sa.Date(), nullable=True))
    op.add_column("lesson_reports", sa.Column("next_lesson_start", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("lesson_reports", "next_lesson_start")
    op.drop_column("lesson_reports", "next_lesson_date")
    op.drop_column("lesson_reports", "next_homework")
    op.drop_column("lesson_reports", "homework_status")
    op.drop_column("lesson_reports", "learning_status")
    op.drop_column("lesson_reports", "material_name")
