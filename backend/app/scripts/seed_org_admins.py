"""指導実績報告システム（legacy）向け 組織管理アカウント作成スクリプト（冪等）。

代々木の 管理責任者(admin_chief)・管理者(admin_master) アカウントを作成する。
- 既存（未削除）アカウントはスキップ。ソフトデリート済みは同一アカウントとして復活。
- パスワードは Passw0rd!（must_change_password=False ＝初回ログイン時の変更を求めない）。
- allowed_systems は ["legacy"]（このシステム専用。両システム共通にはしない）。
- user_no はロール帯で採番（管理者 admin_master=3万番台 / 管理責任者 admin_chief=9万番台）。

招待フローではなく直接作成のため、メール送信は一切行わない（@example.com へのバウンスも発生しない）。

使い方:
    docker compose exec backend python -m app.scripts.seed_org_admins
"""
from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User
from app.services.user_no_service import user_no_for_new_user

PASSWORD = "Passw0rd!"
ALLOWED_SYSTEMS = ["legacy"]

# (email, 表示名, role)
_USERS = [
    ("yoyogi.administrator@example.com", "代々木管理責任者", "admin_chief"),
    ("yoyogi.manager@example.com",       "代々木管理者",     "admin_master"),
]


def _upsert(db, email: str, display_name: str, role: str, password_hash: str) -> None:
    existing = db.scalar(select(User).where(User.email == email))
    if existing and not existing.deleted_at:
        print(f"  SKIP   (already active): {email}")
        return

    user = existing if existing else User(email=email)
    if existing is None:
        db.add(user)
    action = "REVIVE" if existing else "CREATE"

    user.display_name = display_name
    user.role = role
    user.roles = [role]
    user.allowed_systems = list(ALLOWED_SYSTEMS)
    user.password_hash = password_hash
    user.is_active = True
    user.must_change_password = False
    user.deleted_at = None
    user.tutor_no = None
    db.flush()
    if not user.user_no:
        user.user_no = user_no_for_new_user(db, role)
    db.flush()
    print(f"  {action}: No={user.user_no:<6} {email}  {display_name}  role={role}")


def main() -> None:
    db = SessionLocal()
    try:
        password_hash = hash_password(PASSWORD)
        for email, display_name, role in _USERS:
            _upsert(db, email, display_name, role, password_hash)
        db.commit()
        print("seed_org_admins (legacy) complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
