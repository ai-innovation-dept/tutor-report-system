"""add must_change_password to users

新システムのCSV一括作成ユーザー（初期パスワード Passw0rd!）に、初回ログイン時の
パスワード変更を必須化するためのフラグ。既存ユーザーは False（変更不要）。

users テーブルは両システム共有で legacy(backend) の Alembic が管理するため、
列追加はこちらで行う（new_backend は work_* のみ管理）。server_default=false で
既存行を埋め、既存システムは本列を無視できる（無影響）。

Revision ID: 0015_user_must_change_password
Revises: 0014_admin_chief_supervisor
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_user_must_change_password"
down_revision = "0014_admin_chief_supervisor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("users", "must_change_password", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
