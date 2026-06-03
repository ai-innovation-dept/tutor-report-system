"""ユーザー関連のビジネスロジック。採番・認証・ロール判定を集約する。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import Invitation, User

NEW_SYSTEM_ROLES = {"tutor", "school", "sales", "office", "admin_master"}
ALLOWED_INVITATION_ROLES = {"tutor", "school", "sales", "office", "admin_master"}

ROLE_LABELS = {
    "tutor":        "講師",
    "school":       "学校担当",
    "sales":        "営業担当",
    "office":       "事務担当",
    "admin_master": "管理者",
}

# (最小番号, プレフィックス)
_NO_RANGE: dict[str, tuple[int, str]] = {
    "tutor":        (10001, ""),
    "school":       (20001, ""),
    "sales":        (30001, ""),
    "office":       (30001, ""),
    "admin_master": (30001, ""),
}


def generate_user_no(db: Session, role: str) -> str:
    """ロール別番号帯でuser_noを採番する。既存user_noと未受諾招待を参照して重複を防ぐ。"""
    start, prefix = _NO_RANGE.get(role, (10001, ""))

    existing: list[str | None] = list(db.scalars(select(User.user_no).where(User.user_no.is_not(None))).all())
    # tutor は legacy の tutor_no も参照して衝突を防ぐ
    if role == "tutor":
        existing += list(db.scalars(select(User.tutor_no).where(User.tutor_no.is_not(None))).all())

    # 未受諾招待のtutor_noカラム（user_noを格納済み）
    pending: list[str | None] = list(
        db.scalars(
            select(Invitation.tutor_no).where(
                Invitation.tutor_no.is_not(None),
                Invitation.accepted_at.is_(None),
            )
        ).all()
    )

    max_no = start - 1
    for no in [*existing, *pending]:
        s = str(no) if no else ""
        if prefix:
            if not s.startswith(prefix):
                continue
            value = s[len(prefix):]
        else:
            if not s.isdigit():
                continue
            value = s
        try:
            num = int(value)
            if num >= start:
                max_no = max(max_no, num)
        except ValueError:
            continue

    return f"{prefix}{max_no + 1}"


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def authenticate(db: Session, email: str, password: str) -> User | None:
    from app.core.security import verify_password
    user = get_user_by_email(db, email)
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def effective_roles(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def has_new_system_role(user: User) -> bool:
    return any(r in NEW_SYSTEM_ROLES for r in effective_roles(user))


def allowed_systems_for_role(role: str) -> list[str]:
    if role == "admin_master":
        return ["legacy", "new"]
    return ["new"]
