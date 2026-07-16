"""契約に就業場所(work_location)を追加

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-16

契約管理（契約の編集）で登録した就業場所を、講師の報告書一覧（業務連絡表ヘッダー）の
「事業所の所在地」の下へ自動反映（講師側は読取専用）し、参照画面・PDFエクスポートにも
出力するためのカラム。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("work_location", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_assignment_profiles", "work_location")
