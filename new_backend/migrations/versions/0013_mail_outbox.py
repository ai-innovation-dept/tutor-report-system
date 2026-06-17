"""メール送信キュー（アウトボックス）テーブルを追加

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-17

通知メールを即時送信せず、まず work_mail_outbox へ投函(enqueue)し、バックグラウンドの
ドレイナが1通ずつ間隔をあけて送信する設計に変更するためのテーブル。
同時送信・短時間連打によるSMTPアカウントのスパム判定/ロックを防ぐ。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_mail_outbox",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("to_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_work_mail_outbox_to_email", "work_mail_outbox", ["to_email"])
    op.create_index("ix_work_mail_outbox_status", "work_mail_outbox", ["status"])
    op.create_index("ix_work_mail_outbox_created_at", "work_mail_outbox", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_work_mail_outbox_created_at", table_name="work_mail_outbox")
    op.drop_index("ix_work_mail_outbox_status", table_name="work_mail_outbox")
    op.drop_index("ix_work_mail_outbox_to_email", table_name="work_mail_outbox")
    op.drop_table("work_mail_outbox")
