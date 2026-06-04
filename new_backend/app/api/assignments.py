"""assignments テーブル管理 API。新システム専用（system_type='new'）。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.dependencies.auth import get_current_user, require_role
from app.forms.definitions import FORM_REGISTRY
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkReport
from app.services.assignment_service import get_or_create_new_assignment
from app.schemas.assignments import (
    AssignmentCreate,
    AssignmentForSchool,
    AssignmentOut,
    AssignmentPatch,
)

router = APIRouter(prefix="/api/w/assignments", tags=["work-assignments"])


def _form_definition(form_type: str) -> dict:
    form = FORM_REGISTRY.get(form_type) or FORM_REGISTRY["monthly_dispatch"]
    return {
        "form_type": form.form_type,
        "label": form.label,
        "max_lines": form.max_lines,
        "columns": [
            {"key": c.key, "label": c.label, "type": c.type, "summable": c.summable}
            for c in form.columns
        ],
        "summable_keys": sorted(form.summable_keys),
    }


def _assignment_form_type(db: Session, assignment_id) -> str:
    profile = db.scalar(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.assignment_id == assignment_id,
            WorkAssignmentProfile.is_active.is_(True),
        )
    )
    if not profile or profile.form_type not in FORM_REGISTRY:
        return "monthly_dispatch"
    return profile.form_type


def _assignment_out(db: Session, assignment: Assignment) -> dict:
    form_type = _assignment_form_type(db, assignment.id)
    return {
        "id": assignment.id,
        "tutor_id": assignment.tutor_id,
        "student_name": assignment.student_name,
        "is_active": assignment.is_active,
        "system_type": assignment.system_type,
        "created_at": assignment.created_at,
        "tutor": assignment.tutor,
        "form_type": form_type,
        "form_definition": _form_definition(form_type),
        "parent_id": assignment.parent_id,
        "school_name": assignment.parent.display_name if assignment.parent else None,
        "skip_school_approval": assignment.skip_parent_approval,
        "reminder_enabled": assignment.reminder_enabled,
        "reminder_days_after": assignment.reminder_days_after,
        "reminder_count": assignment.reminder_count,
    }


def _get_assignment_out(db: Session, assignment_id) -> dict:
    assignment = db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.id == assignment_id)
    )
    return _assignment_out(db, assignment)


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


@router.post("/for-school", response_model=AssignmentOut)
def get_or_create_for_school(
    payload: AssignmentForSchool,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """講師が学校を選んで報告書を作成できるよう、(講師, 学校) の紐付けを取得または作成する。

    既存の紐付けがあればそれを返し、なければ student_name に学校名を入れて新規作成する。
    これにより講師は複数の学校（＝複数の紐付け）の報告書を並行して作成できる。
    """
    school = db.get(User, payload.school_id)
    school_roles = list(school.roles or []) if school else []
    if not school or "school" not in school_roles:
        raise HTTPException(status_code=422, detail="school_id must be a school user")

    assignment = get_or_create_new_assignment(db, user, school)
    db.commit()
    return _get_assignment_out(db, assignment.id)


@router.get("", response_model=list[AssignmentOut])
def list_assignments(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.system_type == "new")
        .order_by(Assignment.created_at.desc())
    )
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "tutor" in roles:
        stmt = stmt.where(Assignment.tutor_id == user.id, Assignment.is_active.is_(True))
    return [_assignment_out(db, assignment) for assignment in db.scalars(stmt).all()]


@router.patch("/{assignment_id}", response_model=AssignmentOut)
def patch_assignment(
    assignment_id: UUID,
    payload: AssignmentPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    a = db.get(Assignment, assignment_id)
    if not a or a.system_type != "new":
        raise HTTPException(status_code=404, detail="assignment not found")
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    is_master = "admin_master" in roles
    is_tutor = "tutor" in roles
    if not is_master and not is_tutor:
        raise HTTPException(status_code=403, detail="forbidden")
    if is_tutor and not is_master:
        if a.tutor_id != user.id:
            raise HTTPException(status_code=403, detail="not your assignment")
        data = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if k == "parent_id"}
    else:
        data = payload.model_dump(exclude_unset=True)
    _FIELD_MAP = {"skip_school_approval": "skip_parent_approval"}
    for key, value in data.items():
        setattr(a, _FIELD_MAP.get(key, key), value)
    db.commit()
    return _get_assignment_out(db, a.id)


@router.delete("/{assignment_id}", status_code=204)
def delete_assignment(
    assignment_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    a = db.get(Assignment, assignment_id)
    if not a or a.system_type != "new":
        raise HTTPException(status_code=404, detail="assignment not found")
    has_reports = db.scalar(
        select(WorkReport).where(WorkReport.assignment_id == assignment_id).limit(1)
    )
    if has_reports:
        raise HTTPException(status_code=409, detail="reports exist for this assignment")
    db.delete(a)
    db.commit()
