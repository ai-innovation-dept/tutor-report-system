"""add mail_outbox table (send queue)

通知メールを即時送信せず、まず mail_outbox へ投函(enqueue)し、バックグラウンドの
ドレイナが1通ずつ間隔をあけて送信する設計に変更するためのテーブル。
同時送信・短時間連打によるSMTPアカウントのスパム判定/ロックを防ぐ。
新システム(new_backend)の work_mail_outbox と対になる。

Revision ID: 0016_mail_outbox
Revises: 0015_user_must_change_password
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_mail_outbox"
down_revision = "0015_user_must_change_password"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mail_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mail_outbox_to_email", "mail_outbox", ["to_email"])
    op.create_index("ix_mail_outbox_status", "mail_outbox", ["status"])
    op.create_index("ix_mail_outbox_created_at", "mail_outbox", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mail_outbox_created_at", table_name="mail_outbox")
    op.drop_index("ix_mail_outbox_status", table_name="mail_outbox")
    op.drop_index("ix_mail_outbox_to_email", table_name="mail_outbox")
    op.drop_table("mail_outbox")
