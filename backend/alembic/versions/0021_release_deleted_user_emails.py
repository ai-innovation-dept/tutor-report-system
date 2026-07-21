"""削除済みユーザーのメールアドレスを解放する（改修依頼 202607210807 ②）

users.email は両システム共有の一意カラムのため、ソフトデリート済みユーザーがアドレスを
保持したままだと同じアドレスで新規作成・コピー作成ができない（「このメールアドレスは既に
使われています」）。削除時にアドレスを解放する運用へ変更したのに合わせ、既に削除済みの
ユーザーが握っているアドレスもここで解放する（行自体は残す＝過去の報告書・監査ログの
参照整合性は保つ）。

解放後のアドレスは deleted-<id先頭12桁>@deleted.invalid（RFC 2606 の予約TLD＝不達）。
アプリ側の解放処理（user_account_service.release_email_for_deletion /
new_backend user_service.release_email_for_deletion）と同じ形式・同じドメイン。

元アドレスは復元できないため downgrade は no-op（データ保管の役割は無効で運用する方針）。

Revision ID: 0021_release_deleted_user_emails
Revises: 0020_deadline_notice_sends
Create Date: 2026-07-21
"""

from alembic import op


revision = "0021_release_deleted_user_emails"
down_revision = "0020_deadline_notice_sends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
        SET email = 'deleted-' || left(replace(id::text, '-', ''), 12) || '@deleted.invalid'
        WHERE deleted_at IS NOT NULL
          AND email NOT LIKE '%@deleted.invalid'
        """
    )


def downgrade() -> None:
    # 元のメールアドレスは保持していないため復元できない（意図的な no-op）。
    pass
