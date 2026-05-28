"""add user roles

Revision ID: 0009_add_user_roles
Revises: 0008
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_add_user_roles"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("roles", sa.JSON(), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("UPDATE users SET roles = json_build_array(role) WHERE roles IS NULL")
    elif bind.dialect.name == "sqlite":
        op.execute("UPDATE users SET roles = json_array(role) WHERE roles IS NULL")
    else:
        op.execute("UPDATE users SET roles = CONCAT('[\"', role, '\"]') WHERE roles IS NULL")


def downgrade() -> None:
    op.drop_column("users", "roles")
