"""契約に派遣先事業所の所在地(dispatch_place_address)を追加

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-12

報告書一覧の「派遣先事業所の所在地」を契約管理で登録し、
報告書フォームへ自動反映（講師側は読取専用）するためのカラム。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("dispatch_place_address", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("work_assignment_profiles", "dispatch_place_address")
