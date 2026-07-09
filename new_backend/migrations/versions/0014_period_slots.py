"""コマ設定（担当時限の時間割）period_slots を契約に追加

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-09

契約管理(work_assignment_profiles)に period_slots（JSONB・最大10コマ）を追加する。
各要素は {"start": "HH:MM", "end": "HH:MM"}（①から順・重なり不可）。
設定がある契約は、講師の報告書フォームで担当時限の選択コマから
業務開始時刻・担当業務（分）・休憩時間（分）を自動計算する。
未設定（空リスト）の契約は従来ロジック（開始8:40固定・1コマ50分・休憩(コマ数-1)×10分）のまま。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("period_slots", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("work_assignment_profiles", "period_slots")
