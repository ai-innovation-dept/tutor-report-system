"""契約管理 API（経理 admin_master のみ）。

契約は (講師, 学校) ごとに1件で、work_assignment_profiles に格納する。
作成時に (講師, 学校) の assignment を取得/自動作成して紐付ける。
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.dependencies.auth import require_role
from app.models.shared import User
from app.models.work import WorkAssignmentProfile
from app.schemas.contracts import (
    MAX_TASKS,
    ContractCreate,
    ContractForTutorOut,
    ContractOut,
    ContractTask,
    ContractUpdate,
)
from app.services.assignment_service import get_or_create_new_assignment
from app.services.contract_form_service import build_column_definition

router = APIRouter(prefix="/api/w/contracts", tags=["work-contracts"])

_DETAIL_FIELDS = (
    "customer_id", "our_staff", "contract_start", "contract_end",
    "monthly_minutes", "weekly_lessons", "shift_note", "work_content", "has_scoring",
)


def _has_role(user: User | None, role: str) -> bool:
    if not user:
        return False
    return role in (list(user.roles or []) or ([user.role] if user.role else []))


def _tasks_to_columns(profile: WorkAssignmentProfile, tasks: list[ContractTask]) -> None:
    for index in range(1, MAX_TASKS + 1):
        task = tasks[index - 1] if index <= len(tasks) else None
        setattr(profile, f"task_name_{index}", (task.task_name or None) if task else None)
        setattr(profile, f"task_id_{index}", (task.task_id or None) if task else None)
        setattr(profile, f"contract_id_{index}", (task.contract_id or None) if task else None)


def _tasks_from_columns(profile: WorkAssignmentProfile) -> list[ContractTask]:
    tasks: list[ContractTask] = []
    for index in range(1, MAX_TASKS + 1):
        name = getattr(profile, f"task_name_{index}")
        task_id = getattr(profile, f"task_id_{index}")
        contract_id = getattr(profile, f"contract_id_{index}")
        if name or task_id or contract_id:
            tasks.append(ContractTask(task_name=name, task_id=task_id, contract_id=contract_id))
    return tasks


def _to_out(profile: WorkAssignmentProfile) -> ContractOut:
    return ContractOut(
        id=profile.id,
        assignment_id=profile.assignment_id,
        tutor_id=profile.tutor_id,
        school_id=profile.school_id,
        tutor_name=profile.tutor.display_name if profile.tutor else None,
        school_name=profile.school.display_name if profile.school else None,
        customer_id=profile.customer_id,
        our_staff=profile.our_staff,
        contract_start=profile.contract_start,
        contract_end=profile.contract_end,
        monthly_minutes=profile.monthly_minutes,
        weekly_lessons=profile.weekly_lessons,
        shift_note=profile.shift_note,
        work_content=profile.work_content,
        has_scoring=profile.has_scoring,
        tasks=_tasks_from_columns(profile),
        is_active=profile.is_active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _get_profile_loaded(db: Session, profile_id: uuid.UUID) -> WorkAssignmentProfile:
    profile = db.scalar(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.tutor), selectinload(WorkAssignmentProfile.school))
        .where(WorkAssignmentProfile.id == profile_id)
    )
    if not profile:
        raise HTTPException(status_code=404, detail="contract not found")
    return profile


def _resolve_pair(db: Session, tutor_id: uuid.UUID, school_id: uuid.UUID) -> tuple[User, User]:
    tutor = db.get(User, tutor_id)
    if not _has_role(tutor, "tutor"):
        raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    school = db.get(User, school_id)
    if not _has_role(school, "school"):
        raise HTTPException(status_code=422, detail="school_id must be a school user")
    return tutor, school


@router.get("", response_model=list[ContractOut])
def list_contracts(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    profiles = db.scalars(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.tutor), selectinload(WorkAssignmentProfile.school))
        .order_by(WorkAssignmentProfile.created_at.desc())
    ).all()
    return [_to_out(p) for p in profiles]


@router.post("", response_model=ContractOut, status_code=201)
def create_contract(
    payload: ContractCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    if not payload.tasks:
        raise HTTPException(status_code=422, detail="委託業務①は必須です")
    tutor, school = _resolve_pair(db, payload.tutor_id, payload.school_id)

    duplicate = db.scalar(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id,
            WorkAssignmentProfile.school_id == school.id,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="この講師と学校の契約は既に存在します")

    assignment = get_or_create_new_assignment(db, tutor, school)
    profile = WorkAssignmentProfile(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        school_id=school.id,
        form_type="monthly_dispatch",
        contract_meta={},
        is_active=True,
    )
    for field in _DETAIL_FIELDS:
        setattr(profile, field, getattr(payload, field))
    _tasks_to_columns(profile, payload.tasks)
    db.add(profile)
    db.commit()
    return _to_out(_get_profile_loaded(db, profile.id))


@router.get("/for-tutor", response_model=list[ContractForTutorOut])
def list_contracts_for_tutor(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """ログイン中の講師に紐づく契約一覧＋報告書フォーム用の動的列定義を返す。"""
    profiles = db.scalars(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.school))
        .where(WorkAssignmentProfile.tutor_id == user.id, WorkAssignmentProfile.is_active.is_(True))
    ).all()
    return [
        ContractForTutorOut(
            school_id=p.school_id,
            school_name=p.school.display_name if p.school else None,
            customer_id=p.customer_id,
            our_staff=p.our_staff,
            contract_start=p.contract_start,
            contract_end=p.contract_end,
            monthly_minutes=p.monthly_minutes,
            weekly_lessons=p.weekly_lessons,
            shift_note=p.shift_note,
            work_content=p.work_content,
            has_scoring=p.has_scoring,
            tasks=_tasks_from_columns(p),
            column_definition=build_column_definition(p),
        )
        for p in profiles
    ]


@router.get("/{contract_id}", response_model=ContractOut)
def get_contract(
    contract_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    return _to_out(_get_profile_loaded(db, contract_id))


@router.patch("/{contract_id}", response_model=ContractOut)
def update_contract(
    contract_id: uuid.UUID,
    payload: ContractUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    profile = _get_profile_loaded(db, contract_id)
    data = payload.model_dump(exclude_unset=True)

    # 講師・学校の変更（指定時のみ）。変更時は assignment を再解決し重複を確認する。
    new_tutor_id = data.get("tutor_id", profile.tutor_id)
    new_school_id = data.get("school_id", profile.school_id)
    if new_tutor_id != profile.tutor_id or new_school_id != profile.school_id:
        tutor, school = _resolve_pair(db, new_tutor_id, new_school_id)
        duplicate = db.scalar(
            select(WorkAssignmentProfile).where(
                WorkAssignmentProfile.tutor_id == tutor.id,
                WorkAssignmentProfile.school_id == school.id,
                WorkAssignmentProfile.id != profile.id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="この講師と学校の契約は既に存在します")
        assignment = get_or_create_new_assignment(db, tutor, school)
        profile.tutor_id = tutor.id
        profile.school_id = school.id
        profile.assignment_id = assignment.id

    for field in _DETAIL_FIELDS:
        if field in data:
            setattr(profile, field, data[field])
    if "tasks" in data:
        _tasks_to_columns(profile, payload.tasks)

    db.commit()
    return _to_out(_get_profile_loaded(db, profile.id))


@router.delete("/{contract_id}")
def delete_contract(
    contract_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    profile = _get_profile_loaded(db, contract_id)
    profile.is_active = False
    db.commit()
    return {"status": "ok"}
