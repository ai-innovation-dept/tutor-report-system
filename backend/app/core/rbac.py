# === Phase 2: 認証・認可 START ===
from fastapi import Depends, HTTPException, status

from app.deps import get_current_user
from app.models import User


ADMIN_ROLES = {"admin_receiver", "admin_reviewer", "admin_master"}
ALL_ROLES = {"tutor", "parent", "admin_receiver", "admin_reviewer", "admin_master"}
MULTI_ROLE_ALLOWED = {"admin_receiver", "admin_reviewer"}


def user_roles(user: User) -> list[str]:
    roles = list(user.roles or [])
    if not roles and user.role:
        roles = [user.role]
    return roles


def has_role(user: User, role: str) -> bool:
    return role in user_roles(user)


def get_current_role(user: User) -> str:
    return user.role


def is_admin(user: User) -> bool:
    return any(role in ADMIN_ROLES for role in user_roles(user))


def normalize_roles(roles: list[str]) -> list[str]:
    cleaned = []
    for role in roles:
        if role not in ALL_ROLES:
            raise HTTPException(status_code=422, detail=f"invalid role: {role}")
        if role not in cleaned:
            cleaned.append(role)
    if not cleaned:
        raise HTTPException(status_code=422, detail="roles is required")
    if "admin_master" in cleaned and cleaned != ["admin_master"]:
        raise HTTPException(status_code=422, detail="admin_master cannot be combined with other roles")
    if "tutor" in cleaned and cleaned != ["tutor"]:
        raise HTTPException(status_code=422, detail="tutor cannot be combined with other roles")
    if "parent" in cleaned and cleaned != ["parent"]:
        raise HTTPException(status_code=422, detail="parent cannot be combined with other roles")
    if len(cleaned) > 1 and not set(cleaned).issubset(MULTI_ROLE_ALLOWED):
        raise HTTPException(status_code=422, detail="only admin_receiver and admin_reviewer can be combined")
    return cleaned


def primary_role(roles: list[str]) -> str:
    order = ["admin_master", "tutor", "parent", "admin_receiver", "admin_reviewer"]
    role_set = set(roles)
    return next(role for role in order if role in role_set)


def sync_user_roles(user: User, roles: list[str]) -> None:
    normalized = normalize_roles(roles)
    user.roles = normalized
    user.role = primary_role(normalized)


def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return user
    return dependency
# === Phase 2 END ===
