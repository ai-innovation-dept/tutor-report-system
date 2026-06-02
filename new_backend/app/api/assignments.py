"""assignments テーブル管理 API。新システム専用（system_type='new'）。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.models.shared import Assignment, User
from app.schemas.assignments import AssignmentCreate, AssignmentOut, AssignmentPatch

router = APIRouter(prefix="/api/w/assignments", tags=["work-assignments"])


def _get_assignment_out(db: Session, assignment_id) -> Assignment:
    return db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.tutor))
        .where(Assignment.id == assignment_id)
    )


@router.post("", response_model=AssignmentOut, status_code=201)
def create_assignment(
    payload: AssignmentCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    tutor = db.get(User, payload.tutor_id)
    if not tutor or "tutor" not in (list(tutor.roles or []) or [tutor.role]):
        raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")

    duplicate = db.scalar(
        select(Assignment).where(
            Assignment.tutor_id == payload.tutor_id,
            Assignment.student_name == payload.student_name.strip(),
            Assignment.system_type == "new",
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="assignment already exists")

    a = Assignment(
        tutor_id=payload.tutor_id,
        student_name=payload.student_name.strip(),
        system_type="new",
        is_active=True,
    )
    db.add(a)
    db.commit()
    return _get_assignment_out(db, a.id)


@router.get("", response_model=list[AssignmentOut])
def list_assignments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Assignment)
        .options(selectinload(Assignment.tutor))
        .where(Assignment.system_type == "new")
        .order_by(Assignment.created_at.desc())
    )
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "tutor" in roles:
        stmt = stmt.where(Assignment.tutor_id == user.id, Assignment.is_active.is_(True))
    return list(db.scalars(stmt).all())


@router.patch("/{assignment_id}", response_model=AssignmentOut)
def patch_assignment(
    assignment_id: UUID,
    payload: AssignmentPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    a = db.get(Assignment, assignment_id)
    if not a or a.system_type != "new":
        raise HTTPException(status_code=404, detail="assignment not found")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(a, key, value)
    db.commit()
    return _get_assignment_out(db, a.id)
