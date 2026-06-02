"""ユーザー関連のビジネスロジック。"""
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.shared import User

NEW_SYSTEM_ROLES = {"tutor", "school", "sales", "office", "admin_master"}


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
