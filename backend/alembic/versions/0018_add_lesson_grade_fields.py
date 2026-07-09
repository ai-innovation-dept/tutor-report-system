"""add lesson report grade fields (学年：区分＋学年数)

指導報告の内容項目に「学年」を追加する。教科の前に置く先頭項目。
- grade_level: 学年区分（小/中/高）
- grade_year : 学年数（小1〜6・中/高1〜3）
既存行は NULL（過去データは破棄しない・必須化はフロントで強制する）。

Revision ID: 0018_lesson_grade_fields
Revises: 0017_lesson_detail_fields
Create Date: 2026-07-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_lesson_grade_fields"
down_revision = "0017_lesson_detail_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lesson_reports", sa.Column("grade_level", sa.String(length=2), nullable=True))
    op.add_column("lesson_reports", sa.Column("grade_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lesson_reports", "grade_year")
    op.drop_column("lesson_reports", "grade_level")
