"""委託業務をメイン業務（①〜③・①必須）とサブ業務（①〜⑤・任意）に分割

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-12

既存の委託業務①〜③はメイン業務①〜③として継続使用し、
④⑤が登録されている契約はサブ業務①②へ自動移行したうえで旧④⑤カラムを削除する。
報告書の既存スナップショット（task_minutes_4/5 キー）は form_data 内に保全され表示に影響しない。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "work_assignment_profiles"


def upgrade() -> None:
    for i in range(1, 6):
        op.add_column(_TABLE, sa.Column(f"sub_task_name_{i}", sa.String(100), nullable=True))
        op.add_column(_TABLE, sa.Column(f"sub_task_id_{i}", sa.String(50), nullable=True))
        op.add_column(_TABLE, sa.Column(f"sub_contract_id_{i}", sa.String(50), nullable=True))
    # 既存の委託業務④⑤をサブ業務①②へ移行
    op.execute(
        f"""
        UPDATE {_TABLE}
        SET sub_task_name_1 = task_name_4,
            sub_task_id_1 = task_id_4,
            sub_contract_id_1 = contract_id_4,
            sub_task_name_2 = task_name_5,
            sub_task_id_2 = task_id_5,
            sub_contract_id_2 = contract_id_5
        WHERE task_name_4 IS NOT NULL OR task_id_4 IS NOT NULL OR contract_id_4 IS NOT NULL
           OR task_name_5 IS NOT NULL OR task_id_5 IS NOT NULL OR contract_id_5 IS NOT NULL
        """
    )
    for i in (4, 5):
        op.drop_column(_TABLE, f"task_name_{i}")
        op.drop_column(_TABLE, f"task_id_{i}")
        op.drop_column(_TABLE, f"contract_id_{i}")


def downgrade() -> None:
    for i in (4, 5):
        op.add_column(_TABLE, sa.Column(f"task_name_{i}", sa.String(100), nullable=True))
        op.add_column(_TABLE, sa.Column(f"task_id_{i}", sa.String(50), nullable=True))
        op.add_column(_TABLE, sa.Column(f"contract_id_{i}", sa.String(50), nullable=True))
    op.execute(
        f"""
        UPDATE {_TABLE}
        SET task_name_4 = sub_task_name_1,
            task_id_4 = sub_task_id_1,
            contract_id_4 = sub_contract_id_1,
            task_name_5 = sub_task_name_2,
            task_id_5 = sub_task_id_2,
            contract_id_5 = sub_contract_id_2
        """
    )
    for i in range(1, 6):
        op.drop_column(_TABLE, f"sub_task_name_{i}")
        op.drop_column(_TABLE, f"sub_task_id_{i}")
        op.drop_column(_TABLE, f"sub_contract_id_{i}")
