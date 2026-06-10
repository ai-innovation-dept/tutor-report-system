"""採点列の項目名・単位を任意入力化: scoring_label / scoring_unit を追加

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-10

採点専用欄の「項目名（採点）」と「単位（回）」を任意入力にする。分は固定のまま。
既存行は NULL とし、列定義生成時に既定値（採点／回）へフォールバックする。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("work_assignment_profiles", sa.Column("scoring_label", sa.String(50), nullable=True))
    op.add_column("work_assignment_profiles", sa.Column("scoring_unit", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("work_assignment_profiles", "scoring_unit")
    op.drop_column("work_assignment_profiles", "scoring_label")
