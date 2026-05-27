"""add_returned_to_receiver_status

Revision ID: 0008_add_returned_to_receiver_status
Revises: 0007_add_password_reset_tokens
Create Date: 2026-05-27
"""

from alembic import op


revision = "0008_add_returned_to_receiver_status"
down_revision = "0007_add_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Status is stored as String, so adding an enum member requires no DDL.
    pass


def downgrade() -> None:
    op.execute(
        "UPDATE lesson_reports SET status = 'returned_to_tutor' "
        "WHERE status = 'returned_to_receiver'"
    )
    op.execute(
        "UPDATE report_events SET from_status = 'returned_to_tutor' "
        "WHERE from_status = 'returned_to_receiver'"
    )
    op.execute(
        "UPDATE report_events SET to_status = 'returned_to_tutor' "
        "WHERE to_status = 'returned_to_receiver'"
    )
