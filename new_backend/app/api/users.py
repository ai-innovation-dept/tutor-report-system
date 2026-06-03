import math
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password
from app.dependencies.auth import get_current_user, require_role
from app.models.shared import User
from app.schemas.users import UserListOut, UserOut, UserPatch

router = APIRouter(prefix="/api/w/users", tags=["work-users"])


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("", response_model=UserListOut)
def list_users(
    page: int = 1,
    per_page: int = 50,
    role: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    user_roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "admin_master" not in user_roles and role is None:
        raise HTTPException(status_code=403, detail="forbidden")
    page = max(1, page)
    per_page = min(max(1, per_page), 100)
    stmt = select(User).where(User.deleted_at.is_(None))
    if search and search.strip():
        kw = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(func.lower(User.display_name).like(kw), func.lower(User.email).like(kw))
        )
    users = db.scalars(stmt.order_by(User.created_at.desc())).all()
    users = [u for u in users if u.allowed_systems and "new" in u.allowed_systems]
    if role:
        users = [u for u in users if role in (list(u.roles or []) or [u.role])]
    total = len(users)
    start = (page - 1) * per_page
    return UserListOut(items=list(users[start: start + per_page]), total=total)


@router.patch("/{user_id}", response_model=UserOut)
def patch_user(
    user_id: UUID,
    payload: UserPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/reset-password")
def reset_user_password(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    password = secrets.token_urlsafe(10)
    user.password_hash = hash_password(password)
    db.commit()
    return {"initial_password": password}
