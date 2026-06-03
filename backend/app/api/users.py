# === Phase 3: ユーザー管理 START ===
import secrets
import math
from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.rbac import ADMIN_ROLES, has_role, is_admin, require_role, sync_user_roles
from app.core.security import hash_password, verify_password
from app.database import get_db
from app.deps import get_current_user
from app.models import Assignment, Invitation, LessonReport, User
from app.api.invitations import _send_invitation_email, prepare_parent_invitation_for_assignment
from app.schemas import AssignmentCreate, AssignmentOut, AssignmentPatch, PasswordChange, UserCreate, UserListOut, UserOut, UserPatch, UserRolesPatch

router = APIRouter(prefix="/api", tags=["users"])


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="email already exists")
    password = payload.password or secrets.token_urlsafe(10)
    user = User(email=str(payload.email), role=payload.role, display_name=payload.display_name, phone=payload.phone, password_hash=hash_password(password))
    sync_user_roles(user, [payload.role])
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"user": UserOut.model_validate(user), "initial_password": password}


def _parse_roles(roles: str | None, role: str | None) -> set[str]:
    raw = roles or role or ""
    return {item.strip() for item in raw.split(",") if item.strip()}


def _active_admin_master_count(db: Session) -> int:
    return sum(
        1
        for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
        if has_role(user, "admin_master")
    )


def _ensure_not_last_admin_master(user: User, db: Session) -> None:
    if has_role(user, "admin_master") and user.is_active and _active_admin_master_count(db) <= 1:
        raise HTTPException(status_code=409, detail="最後の管理者のため操作できません")


@router.get("/users", response_model=UserListOut)
def list_users(
    page: int = 1,
    per_page: int = 50,
    roles: str | None = None,
    role: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role(*ADMIN_ROLES)),
):
    page = max(1, page)
    per_page = min(max(1, per_page), 100)
    stmt = select(User).where(User.deleted_at.is_(None))
    if search and search.strip():
        keyword = f"%{search.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(User.display_name).like(keyword), func.lower(User.email).like(keyword)))
    users = db.scalars(stmt.order_by(User.created_at.desc())).all()
    users = [user for user in users if getattr(user, "allowed_systems", None) is None or "legacy" in user.allowed_systems]
    role_counts = {key: 0 for key in ["all", "tutor", "parent", "admin_receiver", "admin_reviewer", "admin_master"]}
    role_counts["all"] = len(users)
    for user in users:
        for user_role in user.roles or [user.role]:
            if user_role in role_counts:
                role_counts[user_role] += 1
    selected_roles = _parse_roles(roles, role)
    if selected_roles:
        users = [user for user in users if any(has_role(user, selected_role) for selected_role in selected_roles)]
    total = len(users)
    total_pages = max(1, math.ceil(total / per_page))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * per_page
    return UserListOut(
        items=users[start : start + per_page],
        total=total,
        total_pages=total_pages,
        page=page,
        per_page=per_page,
        role_counts=role_counts,
        active_admin_master_count=_active_admin_master_count(db),
    )


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(user_id: UUID, payload: UserPatch, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    data = payload.model_dump(exclude_unset=True)
    if "role" in data and data["role"]:
        sync_user_roles(user, [data.pop("role")])
    for key, value in data.items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/roles", response_model=UserOut)
def patch_user_roles(user_id: UUID, payload: UserRolesPatch, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    sync_user_roles(user, payload.roles)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/disable")
def disable_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    _ensure_not_last_admin_master(user, db)
    user.is_active = False
    db.commit()
    return {"status": "disabled"}


@router.patch("/users/{user_id}/enable")
def enable_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    user.is_active = True
    db.commit()
    return {"status": "enabled"}


@router.delete("/users/{user_id}")
def delete_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    _ensure_not_last_admin_master(user, db)
    user.deleted_at = datetime.now(timezone.utc)
    user.is_active = False
    db.commit()
    return {"status": "deleted"}


@router.post("/users/me/password")
def change_password(payload: PasswordChange, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current password mismatch")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user or user.deleted_at:
        raise HTTPException(status_code=404, detail="user not found")
    password = secrets.token_urlsafe(10)
    user.password_hash = hash_password(password)
    db.commit()
    return {"initial_password": password}


@router.post("/assignments", response_model=AssignmentOut)
async def create_assignment(payload: AssignmentCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = payload.model_dump()
    parent_email = data.pop("parent_email", None)
    if user.role == "tutor":
        if payload.tutor_id != user.id:
            raise HTTPException(status_code=403, detail="cannot create assignments for another tutor")
        data["parent_id"] = None
    elif user.role != "admin_master":
        raise HTTPException(status_code=403, detail="not allowed")
    else:
        tutor = db.get(User, payload.tutor_id)
        if not tutor or not has_role(tutor, "tutor"):
            raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
        if payload.parent_id:
            parent = db.get(User, payload.parent_id)
            if not parent or not has_role(parent, "parent"):
                raise HTTPException(status_code=422, detail="parent_id must be a parent user")
    if parent_email and data.get("parent_id"):
        raise HTTPException(status_code=422, detail="parent_email and parent_id cannot both be set")
    duplicate = db.scalar(
        select(Assignment).where(
            Assignment.tutor_id == payload.tutor_id,
            Assignment.student_name == payload.student_name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="assignment already exists")
    assignment = Assignment(**data)
    db.add(assignment)
    invitation = None
    should_send = False
    if parent_email:
        db.flush()
        invitation, _, should_send = prepare_parent_invitation_for_assignment(str(parent_email), assignment, db, user)
    db.commit()
    if invitation and should_send:
        invitation = db.scalar(
            select(Invitation)
            .options(selectinload(Invitation.assignment).selectinload(Assignment.tutor))
            .where(Invitation.id == invitation.id)
        )
        await _send_invitation_email(invitation, request)
    assignment = db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.id == assignment.id)
    )
    return assignment


_TUTOR_PATCH_ALLOWED = {"reminder_enabled", "reminder_days_after", "reminder_count"}


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
def patch_assignment(assignment_id: UUID, payload: AssignmentPatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")
    if has_role(current_user, "admin_master"):
        data = payload.model_dump(exclude_unset=True)
    elif has_role(current_user, "tutor"):
        if assignment.tutor_id != current_user.id:
            raise HTTPException(status_code=403, detail="insufficient role")
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if k in _TUTOR_PATCH_ALLOWED}
    else:
        raise HTTPException(status_code=403, detail="insufficient role")
    if "tutor_id" in data and data["tutor_id"] is not None:
        tutor = db.get(User, data["tutor_id"])
        if not tutor or not has_role(tutor, "tutor"):
            raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    if "parent_id" in data and data["parent_id"] is not None:
        parent = db.get(User, data["parent_id"])
        if not parent or not has_role(parent, "parent"):
            raise HTTPException(status_code=422, detail="parent_id must be a parent user")
    for key, value in data.items():
        setattr(assignment, key, value)
    if "parent_id" in data:
        db.query(LessonReport).filter(LessonReport.assignment_id == assignment.id).update({"parent_id": data["parent_id"]}, synchronize_session=False)
    db.commit()
    assignment = db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.id == assignment.id)
    )
    return assignment


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Assignment).options(selectinload(Assignment.tutor), selectinload(Assignment.parent)).order_by(Assignment.created_at.desc())
    if user.role == "tutor":
        stmt = stmt.where(Assignment.tutor_id == user.id, Assignment.is_active.is_(True))
    elif user.role == "parent":
        stmt = stmt.where(Assignment.parent_id == user.id, Assignment.is_active.is_(True))
    elif not is_admin(user):
        raise HTTPException(status_code=403, detail="not allowed")
    return db.scalars(stmt).all()
# === Phase 3 END ===
