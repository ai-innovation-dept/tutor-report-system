"""契約に契約管理番号(contract_no)を追加＋既存契約へ作成順に発番

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-17

契約管理番号（202607170952）。契約の作成順に1から連番で自動発番し、契約管理の一覧・
編集ドロワー・CSVエクスポート（参考列）に表示する。採番は「現在の最大値+1」＝途中の
欠番（物理削除済み契約の番号）は再利用しない。既存契約には created_at 昇順（同時刻は id 順）で発番する。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("contract_no", sa.Integer(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_work_profile_contract_no", "work_assignment_profiles", ["contract_no"]
    )
    # 既存契約へ作成順（created_at 昇順・同時刻は id 順）に 1 から発番する
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id FROM work_assignment_profiles ORDER BY created_at ASC, id ASC"
    )).fetchall()
    for number, row in enumerate(rows, start=1):
        conn.execute(
            sa.text("UPDATE work_assignment_profiles SET contract_no = :number WHERE id = :pid"),
            {"number": number, "pid": row[0]},
        )


def downgrade() -> None:
    op.drop_constraint("uq_work_profile_contract_no", "work_assignment_profiles", type_="unique")
    op.drop_column("work_assignment_profiles", "contract_no")
