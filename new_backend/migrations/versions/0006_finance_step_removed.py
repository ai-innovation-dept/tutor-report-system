"""承認フロー変更: 経理ステップ廃止（営業を最終承認化）に伴うデータ移行

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-09

承認フローを「講師→学校→事務→営業→経理」から「講師→学校→事務→営業」に変更し、
営業承認で完了（approved）とする。これに伴い、フロー変更時点で「経理確認待ち
（awaiting_finance）」に滞留している報告書は、最終承認の担当工程が無くなり宙に浮く。
依頼により、これらは強制的に完了（approved）へ移行する（経理の最終チェックは省略）。
冪等：対象が無ければ何も変更しない。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 経理確認待ちの報告書を完了にし、承認担当ロールをクリアする
    op.execute(
        "UPDATE work_reports "
        "SET status = 'approved', current_approver_role = NULL "
        "WHERE status = 'awaiting_finance'"
    )


def downgrade() -> None:
    # データ移行のため不可逆（巻き戻しは行わない）
    pass
