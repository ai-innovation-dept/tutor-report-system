"""月時間（分）・週コマの期間付き複数ケース(workload_cases)を追加

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-12

契約の月時間（分）・週コマを単一値から「期間付きの複数ケース」へ拡張する。
既存の monthly_minutes / weekly_lessons が登録済みの契約は、
契約期間（contract_start〜contract_end）を適用期間とした1ケース目へ自動移行する。
旧カラムはCSV取込の入力互換用に残す。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_assignment_profiles",
        sa.Column("workload_cases", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    # 既存の単一値を「適用期間＝契約期間」の1ケース目として移行する
    op.execute(
        """
        UPDATE work_assignment_profiles
        SET workload_cases = jsonb_build_array(jsonb_build_object(
            'monthly_minutes', monthly_minutes,
            'weekly_lessons', weekly_lessons,
            'start_date', to_char(contract_start, 'YYYY-MM-DD'),
            'end_date', to_char(contract_end, 'YYYY-MM-DD')
        ))
        WHERE monthly_minutes IS NOT NULL OR weekly_lessons IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("work_assignment_profiles", "workload_cases")
