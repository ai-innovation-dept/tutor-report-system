"""月時間・週コマのケースを担当業務に紐づけ（task_index付与）

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-12

workload_cases の各ケースに task_index（担当業務①〜③＝1..3）を持たせ、
担当業務ごとの月分上限（超過判定・要望連絡事項の業務別表示）に使用する。
既存ケース（task_index無し）は担当業務①に紐づける。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 既存ケースに task_index=1（担当業務①）を付与する（既に持つ場合は上書きしない）
    op.execute(
        """
        UPDATE work_assignment_profiles
        SET workload_cases = (
            SELECT jsonb_agg(
                CASE WHEN case_item ? 'task_index' THEN case_item
                     ELSE case_item || '{"task_index": 1}'::jsonb END)
            FROM jsonb_array_elements(workload_cases) AS case_item
        )
        WHERE jsonb_typeof(workload_cases) = 'array' AND jsonb_array_length(workload_cases) > 0
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE work_assignment_profiles
        SET workload_cases = (
            SELECT jsonb_agg(case_item - 'task_index')
            FROM jsonb_array_elements(workload_cases) AS case_item
        )
        WHERE jsonb_typeof(workload_cases) = 'array' AND jsonb_array_length(workload_cases) > 0
        """
    )
