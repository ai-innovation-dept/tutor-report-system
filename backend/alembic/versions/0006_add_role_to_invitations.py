"""add_role_to_invitations

Revision ID: 0006_add_role_to_invitations
Revises: 0005_add_invitations_table
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_add_role_to_invitations"
down_revision = "0005_add_invitations_table"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("invitations", "role"):
        op.add_column("invitations", sa.Column("role", sa.String(length=32), nullable=False, server_default="parent"))
    if not _has_column("invitations", "display_name"):
        op.add_column("invitations", sa.Column("display_name", sa.String(length=100), nullable=True))
    if not _has_column("invitations", "tutor_no"):
        op.add_column("invitations", sa.Column("tutor_no", sa.String(length=20), nullable=True))


def downgrade() -> None:
    if _has_column("invitations", "tutor_no"):
        op.drop_column("invitations", "tutor_no")
    if _has_column("invitations", "display_name"):
        op.drop_column("invitations", "display_name")
