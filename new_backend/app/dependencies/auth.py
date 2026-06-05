"""
認証・認可の依存関係。ロール権限チェックはここに集約する。
"""
import uuid as _uuid

from fastapi import Cookie, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.shared import User

# 新システムで有効なロール
NEW_SYSTEM_ROLES = {"tutor", "school", "sales", "office", "admin_master"}


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    access_token: str | None = Cookie(default=None, alias="w_access_token"),
) -> User:
    # Authorization ヘッダを Cookie より優先する（テスト・API クライアント対応）
    auth_header = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    token = auth_header or access_token or ""
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(status_code=401, detail="invalid token payload")
    try:
        user_id = _uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid token payload")

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="user not found or inactive")

    return user


def get_active_role(
    user: User = Depends(get_current_user),
    selected_role: str | None = Cookie(default=None, alias="w_selected_role"),
) -> str:
    """JWT + Cookie から有効なロールを解決する。"""
    roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
    if selected_role and selected_role in roles:
        return selected_role
    return roles[0] if roles else user.role


def require_role(*required_roles: str):
    """指定ロールのいずれかを持つユーザーのみ許可する依存関係ファクトリ。"""
    role_set = set(required_roles)

    def _check(
        user: User = Depends(get_current_user),
        active_role: str = Depends(get_active_role),
    ) -> User:
        roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
        if not role_set.intersection(roles):
            raise HTTPException(status_code=403, detail="insufficient role")
        return user

    return _check


def require_new_system_access(user: User = Depends(get_current_user)) -> User:
    """新システムへのアクセス権を持つユーザーのみ許可。"""
    roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
    if not any(r in NEW_SYSTEM_ROLES for r in roles):
        raise HTTPException(status_code=403, detail="new system access not granted")
    return user


def has_role(user: User, role: str) -> bool:
    roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
    return role in roles


def is_admin(user: User) -> bool:
    roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
    return bool({"sales", "office", "admin_master"}.intersection(roles))
