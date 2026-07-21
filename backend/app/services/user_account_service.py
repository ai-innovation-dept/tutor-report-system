"""ユーザーアカウントの削除・コピー新規登録のビジネスロジック（既存システム=legacy）。

新システム（new_backend/app/services/user_service.py）と同一仕様の処理を置く。
users テーブルは両システム共有のため、片方だけ挙動を変えないこと。
"""
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import User
from app.services.user_import_service import INITIAL_PASSWORD, allowed_systems_for_role
from app.services.user_no_service import generate_user_no

# 削除済みユーザーのメール解放先ドメイン。RFC 2606 の予約TLD(.invalid)＝実在せず配送されない。
DELETED_EMAIL_DOMAIN = "deleted.invalid"


def release_email_for_deletion(user: User) -> str:
    """削除したユーザーのメールアドレスを解放する（改修依頼 202607210807 ②）。

    users.email は両システム共有の一意カラムのため、ソフトデリートしたままではそのアドレスで
    新規作成・コピー作成ができない。削除時に不達のダミーアドレスへ書き換えて解放し、
    同じアドレスを「別アカウント」として登録し直せるようにする（削除済みユーザーの復活は行わない）。
    行そのものは残す（過去の報告書・監査ログからの参照整合性を保つため）。
    ※ new_backend/app/services/user_service.release_email_for_deletion と同一仕様。
      変更時は両方を更新すること。
    """
    user.email = f"deleted-{uuid4().hex[:12]}@{DELETED_EMAIL_DOMAIN}"
    return user.email


def copy_user(db: Session, source: User, email: str, display_name: str) -> User:
    """既存ユーザーを複製して新規作成する（改修依頼 202607210807 既存システム①）。

    氏名・メールは新規に指定し、ロール（複数ロール含む）・利用システム・保護者承認スキップ設定を
    コピー元から複製する。招待メールは送らず直接作成し、初期パスワード(Passw0rd!)＋初回ログイン時の
    変更必須を設定する。番号はコピー元の主ロールの番号帯で自動採番する。
    電話番号などの個人情報と担当（assignments）は複製しない。
    ※ new_backend/app/services/user_service.copy_user と同一仕様（新システム側は学校の締め日設定も複製）。
    呼び出し側で氏名・メール重複・ロール権限を検証済みであること。
    """
    role = source.role
    user_no = generate_user_no(db, role)
    user = User(
        email=email.strip().lower(),
        role=role,
        roles=list(source.roles) if source.roles else [role],
        display_name=display_name.strip(),
        user_no=user_no,
        tutor_no=user_no if role == "tutor" else None,
        allowed_systems=(
            list(source.allowed_systems) if source.allowed_systems else allowed_systems_for_role(role)
        ),
        skip_parent_approval=source.skip_parent_approval,
        password_hash=hash_password(INITIAL_PASSWORD),
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    return user
