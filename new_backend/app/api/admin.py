"""管理者向けAPI（ユーザー管理・割り当てプロファイル管理）。"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import require_role
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
from app.schemas.users import UserOut

router = APIRouter(prefix="/api/w/admin", tags=["work-admin"])


class _ProfileIn:
    pass


from pydantic import BaseModel


class ProfileCreate(BaseModel):
    assignment_id: uuid.UUID
    form_type: str = "monthly_dispatch"
    contract_meta: dict = {}


class ProfileOut(BaseModel):
    id: uuid.UUID
    assignment_id: uuid.UUID
    form_type: str
    contract_meta: dict
    is_active: bool

    model_config = {"from_attributes": True}


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(
    payload: ProfileCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "office")),
):
    assignment = db.get(Assignment, payload.assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")
    if not assignment.parent_id:
        raise HTTPException(status_code=422, detail="assignment must have a school (parent) to create a profile")
    existing = db.scalar(select(WorkAssignmentProfile).where(WorkAssignmentProfile.assignment_id == payload.assignment_id))
    if existing:
        raise HTTPException(status_code=409, detail="profile already exists for this assignment")
    profile = WorkAssignmentProfile(
        assignment_id=payload.assignment_id,
        tutor_id=assignment.tutor_id,
        school_id=assignment.parent_id,
        form_type=payload.form_type,
        contract_meta=payload.contract_meta,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "office", "sales")),
):
    return list(db.scalars(select(WorkAssignmentProfile)))


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
def patch_profile(
    profile_id: uuid.UUID,
    payload: dict,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "office")),
):
    profile = db.get(WorkAssignmentProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="profile not found")
    if "contract_meta" in payload:
        profile.contract_meta = payload["contract_meta"]
    if "form_type" in payload:
        profile.form_type = payload["form_type"]
    db.commit()
    db.refresh(profile)
    return profile
