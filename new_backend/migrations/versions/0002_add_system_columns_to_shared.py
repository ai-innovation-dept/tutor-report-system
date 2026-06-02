"""add system_type to assignments, user_no and allowed_systems to users, backfill tutor_no

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # assignments に system_type を追加
    op.add_column("assignments", sa.Column("system_type", sa.String(10), nullable=True))
    op.execute("UPDATE assignments SET system_type = 'legacy'")
    op.alter_column("assignments", "system_type", nullable=False, server_default="legacy")

    # users に user_no と allowed_systems を追加
    op.add_column("users", sa.Column("user_no", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("allowed_systems", postgresql.JSONB(), nullable=True))

    # allowed_systems のデフォルト設定
    op.execute("UPDATE users SET allowed_systems = '[\"legacy\"]'::jsonb WHERE allowed_systems IS NULL")

    # tutor_no のバックフィル（冪等性保証）
    # 1000未満の数値のみ対象: T001 → T1001 形式へ変換、user_no にも同値をセット
    op.execute("""
        UPDATE users
        SET
            tutor_no = 'T' || (CAST(REGEXP_REPLACE(tutor_no, '^T', '') AS INTEGER) + 1000)::text,
            user_no  = 'T' || (CAST(REGEXP_REPLACE(tutor_no, '^T', '') AS INTEGER) + 1000)::text
        WHERE
            role = 'tutor'
            AND tutor_no IS NOT NULL
            AND tutor_no ~ '^T[0-9]+$'
            AND CAST(REGEXP_REPLACE(tutor_no, '^T', '') AS INTEGER) < 1000
    """)

    # tutor_no が既に 1000 以上の tutor には user_no = tutor_no をセット
    op.execute("""
        UPDATE users
        SET user_no = tutor_no
        WHERE
            role = 'tutor'
            AND tutor_no IS NOT NULL
            AND user_no IS NULL
    """)

    # admin_master には両システムへのアクセスを付与
    op.execute("""
        UPDATE users
        SET allowed_systems = '[\"legacy\",\"new\"]'::jsonb
        WHERE 'admin_master' = ANY(ARRAY(SELECT jsonb_array_elements_text(roles)))
           OR role = 'admin_master'
    """)


def downgrade() -> None:
    op.drop_column("users", "allowed_systems")
    op.drop_column("users", "user_no")
    op.drop_column("assignments", "system_type")
