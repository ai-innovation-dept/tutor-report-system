"""教室名＋報告書の表示項目フラグ5件を契約に追加

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-15

契約管理(work_assignment_profiles)に以下を追加する。
- classroom_name: 教室名（報告書の「事業所の名称」の隣に表示・講師読取専用）
- show_*（5件）: 報告書フォームの項目表示/非表示フラグ。既定は全て表示(true)。
  事業所所在地 / 従事業務内容 / 定期代 / 休憩時間 / スケジュール欄。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SHOW_FLAGS = (
    "show_dispatch_address",
    "show_work_content",
    "show_commuter_pass",
    "show_break_minutes",
    "show_schedule_note",
)


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("classroom_name", sa.String(length=100), nullable=True),
    )
    for flag in _SHOW_FLAGS:
        # 既存契約は「全て表示」を維持するため server_default=true
        op.add_column(
            "work_assignment_profiles",
            sa.Column(flag, sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def downgrade() -> None:
    for flag in reversed(_SHOW_FLAGS):
        op.drop_column("work_assignment_profiles", flag)
    op.drop_column("work_assignment_profiles", "classroom_name")
