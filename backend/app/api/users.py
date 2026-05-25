# === Phase 3: ユーザー管理 START ===
import secrets
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import ADMIN_ROLES, require_role
from app.core.security import hash_password, verify_password
from app.database import get_db
from app.deps import get_current_user
from app.models import Assignment, LessonReport, User
from app.schemas import AssignmentCreate, AssignmentOut, AssignmentPatch, PasswordChange, UserCreate, UserOut, UserPatch

router = APIRouter(prefix="/api", tags=["users"])


@router.post("/users")
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="email already exists")
    password = payload.password or secrets.token_urlsafe(10)
    user = User(email=str(payload.email), role=payload.role, display_name=payload.display_name, phone=payload.phone, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"user": UserOut.model_validate(user), "initial_password": password}


@router.get("/users", response_model=list[UserOut])
def list_users(role: str | None = None, db: Session = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    stmt = select(User).order_by(User.created_at.desc())
    if role:
        stmt = stmt.where(User.role == role)
    return db.scalars(stmt).all()


@router.get("/users/{user_id}", response_model=UserOut)
def get_user(user_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role(*ADMIN_ROLES))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def patch_user(user_id: UUID, payload: UserPatch, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return user


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
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    password = secrets.token_urlsafe(10)
    user.password_hash = hash_password(password)
    db.commit()
    return {"initial_password": password}


@router.post("/assignments", response_model=AssignmentOut)
def create_assignment(payload: AssignmentCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = payload.model_dump()
    if user.role == "tutor":
        if payload.tutor_id != user.id:
            raise HTTPException(status_code=403, detail="cannot create assignments for another tutor")
        data["parent_id"] = None
    elif user.role != "admin_master":
        raise HTTPException(status_code=403, detail="not allowed")
    else:
        tutor = db.get(User, payload.tutor_id)
        if not tutor or tutor.role != "tutor":
            raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
        if payload.parent_id:
            parent = db.get(User, payload.parent_id)
            if not parent or parent.role != "parent":
                raise HTTPException(status_code=422, detail="parent_id must be a parent user")
    assignment = Assignment(**data)
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


@router.patch("/assignments/{assignment_id}", response_model=AssignmentOut)
def patch_assignment(assignment_id: UUID, payload: AssignmentPatch, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    assignment = db.get(Assignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")
    data = payload.model_dump(exclude_unset=True)
    if "tutor_id" in data and data["tutor_id"] is not None:
        tutor = db.get(User, data["tutor_id"])
        if not tutor or tutor.role != "tutor":
            raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    if "parent_id" in data and data["parent_id"] is not None:
        parent = db.get(User, data["parent_id"])
        if not parent or parent.role != "parent":
            raise HTTPException(status_code=422, detail="parent_id must be a parent user")
    for key, value in data.items():
        setattr(assignment, key, value)
    if "parent_id" in data:
        db.query(LessonReport).filter(LessonReport.assignment_id == assignment.id).update({"parent_id": data["parent_id"]}, synchronize_session=False)
    db.commit()
    db.refresh(assignment)
    return assignment


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(Assignment).order_by(Assignment.created_at.desc())
    if user.role == "tutor":
        stmt = stmt.where(Assignment.tutor_id == user.id)
    elif user.role == "parent":
        stmt = stmt.where(Assignment.parent_id == user.id)
    elif not user.role.startswith("admin_"):
        raise HTTPException(status_code=403, detail="not allowed")
    return db.scalars(stmt).all()
# === Phase 3 END ===
