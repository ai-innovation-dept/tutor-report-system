"""委託業務に入力形式(task_format)を追加し has_scoring を廃止する

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-05

採点列(has_scoring)機能を廃止し、採点は委託業務①〜⑤として登録する運用に一本化する。
各委託業務に入力形式(task_format)を持たせる:
  - 'minutes'       … 「業務名（分）」1列（デフォルト）
  - 'count_minutes' … 「業務名（回）」＋「業務名（分）」2列
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for i in range(1, 6):
        op.add_column(
            "work_assignment_profiles",
            sa.Column(f"task_format_{i}", sa.String(20), nullable=True, server_default="minutes"),
        )
    op.drop_column("work_assignment_profiles", "has_scoring")


def downgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("has_scoring", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for i in range(1, 6):
        op.drop_column("work_assignment_profiles", f"task_format_{i}")
