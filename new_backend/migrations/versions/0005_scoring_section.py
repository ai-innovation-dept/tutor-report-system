"""採点を専用欄に分離: task_format を廃止し scoring 専用カラムを追加

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-05

委託業務①〜⑤は常に「分のみ」とし、入力形式(task_format)を廃止。
採点は専用欄（採点を追加する／しない＋委託業務ID／個別契約ID）として分離し、
有効時のみ報告書に「採点（回）」列（1セル併記＝回数＋分数固定）を生成する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("scoring_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("work_assignment_profiles", sa.Column("scoring_task_id", sa.String(50), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("scoring_contract_id", sa.String(50), nullable=True))
    for i in range(1, 6):
        op.drop_column("work_assignment_profiles", f"task_format_{i}")


def downgrade() -> None:
    for i in range(1, 6):
        op.add_column(
            "work_assignment_profiles",
            sa.Column(f"task_format_{i}", sa.String(20), nullable=True, server_default="minutes"),
        )
    op.drop_column("work_assignment_profiles", "scoring_contract_id")
    op.drop_column("work_assignment_profiles", "scoring_task_id")
    op.drop_column("work_assignment_profiles", "scoring_enabled")
