"""契約にコマ設定の使用/未使用(use_period_slots)を追加

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-17

契約管理の編集でコマ設定（担当時限の時間割）の使用/未使用を切り替えるフラグ（202607170831）。
True=使用（従来どおり）。False=未使用: コマ設定はグレイアウトで編集不可（値は保持）となり、
講師の報告書フォームは担当時限列なしの手入力方式（業務開始時間・担当業務・副担当業務・
休憩時間を手入力→終了時間のみ自動計算）になる。既存契約はすべて True（従来動作を維持）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("use_period_slots", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("work_assignment_profiles", "use_period_slots")
