"""add user deleted_at

Revision ID: 0010_add_user_deleted_at
Revises: 0009_add_user_roles
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_add_user_deleted_at"
down_revision = "0009_add_user_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "deleted_at")
