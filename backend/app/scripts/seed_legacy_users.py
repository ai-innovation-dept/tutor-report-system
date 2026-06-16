"""指導実績報告システム（legacy）ユーザー一括登録スクリプト。

既存ユーザーはスキップ（email一致・削除されていない場合）。
削除済みユーザーは同一アカウントとして復活させる。
保護者は講師・生徒名の担当も合わせて作成または再利用する。

使い方:
    docker compose exec backend python -m app.scripts.seed_legacy_users
"""
from sqlalchemy import or_, select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import Assignment, User
from app.services.user_no_service import generate_user_no, user_no_for_new_user

PASSWORD = "Passw0rd!"

_USERS = [
    {
        "email": "kintaikanri.tutor1@gmail.com",
        "display_name": "講師太郎",
        "role": "tutor",
        "roles": ["tutor"],
        "allowed_systems": ["legacy", "new"],
    },
    {
        "email": "kintaikanri.tutor1+parent1@gmail.com",
        "display_name": "保護者花子",
        "role": "parent",
        "roles": ["parent"],
        "allowed_systems": ["legacy"],
        "assignment_tutor_email": "kintaikanri.tutor1@gmail.com",
        "student_name": "生徒花子",
    },
    {
        "email": "kintaikanri.tutor1+receiver1@gmail.com",
        "display_name": "受付太郎",
        "role": "admin_receiver",
        "roles": ["admin_receiver", "admin_reviewer"],
        "allowed_systems": ["legacy"],
    },
    {
        "email": "kintaikanri.tutor1+reviewer1@gmail.com",
        "display_name": "再鑑花子",
        "role": "admin_reviewer",
        "roles": ["admin_reviewer"],
        "allowed_systems": ["legacy"],
    },
    {
        "email": "kintaikanri.tutor1+master2@gmail.com",
        "display_name": "管理太郎",
        "role": "admin_master",
        "roles": ["admin_master"],
        "allowed_systems": ["legacy", "new"],
    },
    {
        "email": "kintaikanri.tutor1+supervisor2@gmail.com",
        "display_name": "管責花子",
        "role": "admin_chief",
        "roles": ["admin_chief"],
        "allowed_systems": ["legacy", "new"],
    },
]


def _upsert_user(db, u: dict, password_hash: str) -> User:
    existing = db.scalar(select(User).where(User.email == u["email"]))

    if existing and not existing.deleted_at:
        print(f"  SKIP  (already active): {u['email']}")
        return existing

    if existing and existing.deleted_at:
        # 削除済みアカウントを復活
        user = existing
        user.deleted_at = None
        user.is_active = True
        user.display_name = u["display_name"]
        user.role = u["role"]
        user.roles = u["roles"]
        user.allowed_systems = u["allowed_systems"]
        user.password_hash = password_hash
        user.must_change_password = False
        if u["role"] == "tutor" and not user.tutor_no:
            user.tutor_no = generate_user_no(db, "tutor")
        if not user.user_no:
            user.user_no = user_no_for_new_user(db, u["role"], user.tutor_no)
        db.flush()
        print(f"  REVIVE: {u['email']} roles={u['roles']}")
        return user

    # 新規作成
    tutor_no = generate_user_no(db, "tutor") if u["role"] == "tutor" else None
    user = User(
        email=u["email"],
        display_name=u["display_name"],
        role=u["role"],
        roles=u["roles"],
        allowed_systems=u["allowed_systems"],
        password_hash=password_hash,
        is_active=True,
        must_change_password=False,
        tutor_no=tutor_no,
    )
    db.add(user)
    db.flush()
    user.user_no = user_no_for_new_user(db, u["role"], user.tutor_no)
    db.flush()
    print(f"  CREATE: {u['email']} roles={u['roles']}")
    return user


def _link_assignment(db, user: User, tutor_email: str, student_name: str) -> None:
    tutor = db.scalar(select(User).where(User.email == tutor_email))
    if not tutor:
        print(f"  ERROR: tutor not found ({tutor_email})")
        return

    # 既存担当を探す（削除済み保護者紐付き含む）
    assignment = db.scalar(
        select(Assignment).where(
            Assignment.tutor_id == tutor.id,
            Assignment.student_name == student_name,
            Assignment.is_active.is_(True),
            or_(Assignment.system_type != "new", Assignment.system_type.is_(None)),
        )
    )
    if assignment:
        assignment.parent_id = user.id
        print(f"  LINK  assignment: 講師={tutor.display_name} 生徒={student_name}")
    else:
        db.add(Assignment(
            tutor_id=tutor.id,
            student_name=student_name,
            parent_id=user.id,
            is_active=True,
        ))
        print(f"  CREATE assignment: 講師={tutor.display_name} 生徒={student_name}")


def main() -> None:
    db = SessionLocal()
    try:
        password_hash = hash_password(PASSWORD)
        for u in _USERS:
            user = _upsert_user(db, u, password_hash)
            if "assignment_tutor_email" in u:
                _link_assignment(db, user, u["assignment_tutor_email"], u["student_name"])
        db.commit()
        print("Done.")
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
