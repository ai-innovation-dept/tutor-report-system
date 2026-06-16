"""add admin_chief supervisor user

Revision ID: 0014_admin_chief_supervisor
Revises: 0013_user_skip_parent
Create Date: 2026-06-09
"""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op


revision = "0014_admin_chief_supervisor"
down_revision = "0013_user_skip_parent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import os

    # 本番では検証用の固定ユーザー(supervisor@example.com・既知パスワード)を投入しない。
    # 既存環境は適用済みのため再実行されず無影響。新規の本番DBにテストユーザーが入るのを防ぐ。
    # 本番のクリーン構築は app.scripts.seed_production を使用する。
    if os.getenv("ENVIRONMENT", "development").lower() == "production":
        return

    from passlib.context import CryptContext

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    password_hash = pwd_context.hash("Passw0rd!")

    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT id FROM users WHERE email = 'supervisor@example.com'"))
    if result.fetchone():
        return

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn.execute(
        sa.text(
            "INSERT INTO users (id, email, role, roles, display_name, user_no, allowed_systems, "
            "password_hash, is_active, skip_parent_approval, created_at, updated_at) "
            "VALUES (:id, :email, :role, CAST(:roles AS json), :display_name, :user_no, "
            "CAST(:allowed_systems AS json), :password_hash, :is_active, :skip_parent_approval, "
            ":created_at, :updated_at)"
        ),
        {
            "id": user_id,
            "email": "supervisor@example.com",
            "role": "admin_chief",
            "roles": json.dumps(["admin_chief"]),
            "display_name": "管理責任者1",
            "user_no": "90001",
            "allowed_systems": json.dumps(["legacy", "new"]),
            "password_hash": password_hash,
            "is_active": True,
            "skip_parent_approval": False,
            "created_at": now,
            "updated_at": now,
        },
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM users WHERE email = 'supervisor@example.com'"))
