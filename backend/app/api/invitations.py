# === Phase 3: 招待管理 START ===
import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.rbac import require_role
from app.database import get_db
from app.models import Assignment, Invitation, User
from app.schemas import InvitationCreate, InvitationOut
from app.services.notification_service import EmailChannel

router = APIRouter(prefix="/api/invitations", tags=["invitations"])

INVITATION_SUBJECT = "【指導実績報告システム】保護者アカウントのご案内"


def _invitation_out(invitation: Invitation, message: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        assignment_id=invitation.assignment_id,
        tutor_id=invitation.assignment.tutor_id if invitation.assignment else None,
        tutor_name=invitation.assignment.tutor.display_name if invitation.assignment and invitation.assignment.tutor else None,
        student_name=invitation.assignment.student_name if invitation.assignment else None,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        created_at=invitation.created_at,
        message=message,
    )


def _base_url(request: Request) -> str:
    return settings.base_url.rstrip("/") or str(request.base_url).rstrip("/")


def _invitation_body(invitation: Invitation, request: Request) -> str:
    template = Path("app/templates/email/invitation.txt").read_text(encoding="utf-8")
    return template.format(
        display_name="保護者",
        base_url=_base_url(request),
        token=invitation.token,
        tutor_name=invitation.assignment.tutor.display_name if invitation.assignment and invitation.assignment.tutor else "未設定",
        student_name=invitation.assignment.student_name if invitation.assignment else "未設定",
    )


def _send_invitation_email(invitation: Invitation, request: Request) -> None:
    asyncio.run(EmailChannel().send(invitation.email, INVITATION_SUBJECT, _invitation_body(invitation, request)))


@router.post("", response_model=InvitationOut)
def create_invitation(
    payload: InvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin_master")),
):
    email = str(payload.email).lower()
    tutor = db.get(User, payload.tutor_id)
    if not tutor or tutor.role != "tutor":
        raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    student_name = payload.student_name.strip()
    if not student_name:
        raise HTTPException(status_code=422, detail="student_name is required")

    now = datetime.now(timezone.utc)
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and existing_user.role != "parent":
        raise HTTPException(status_code=409, detail="このメールアドレスは別のロールで登録済みです")
    if existing_user:
        assignment = Assignment(tutor_id=payload.tutor_id, student_name=student_name, parent_id=existing_user.id, is_active=True)
        db.add(assignment)
        db.flush()
        invitation = Invitation(
            email=email,
            role="parent",
            assignment_id=assignment.id,
            token=secrets.token_urlsafe(32),
            invited_by=user.id,
            expires_at=now + timedelta(hours=72),
            accepted_at=now,
            created_at=now,
        )
        db.add(invitation)
        db.commit()
        invitation = db.scalar(
            select(Invitation)
            .options(selectinload(Invitation.assignment).selectinload(Assignment.tutor))
            .where(Invitation.id == invitation.id)
        )
        return _invitation_out(invitation, "既存の保護者アカウントに生徒を紐付けました")

    invitation = db.scalar(
        select(Invitation)
        .options(selectinload(Invitation.assignment).selectinload(Assignment.tutor))
        .where(Invitation.email == email, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    assignment = None
    if invitation:
        assignment = invitation.assignment
        if assignment is None or assignment.parent_id is not None:
            assignment = Assignment(tutor_id=payload.tutor_id, student_name=student_name, parent_id=None, is_active=True)
            db.add(assignment)
            db.flush()
        else:
            assignment.tutor_id = payload.tutor_id
            assignment.student_name = student_name
            assignment.parent_id = None
            assignment.is_active = True
        invitation.assignment_id = assignment.id
        invitation.role = "parent"
        invitation.token = secrets.token_urlsafe(32)
        invitation.invited_by = user.id
        invitation.expires_at = now + timedelta(hours=72)
        invitation.created_at = now
    else:
        assignment = Assignment(tutor_id=payload.tutor_id, student_name=student_name, parent_id=None, is_active=True)
        db.add(assignment)
        db.flush()
        invitation = Invitation(
            email=email,
            role="parent",
            assignment_id=assignment.id,
            token=secrets.token_urlsafe(32),
            invited_by=user.id,
            expires_at=now + timedelta(hours=72),
            created_at=now,
        )
        db.add(invitation)
    db.commit()
    invitation = db.scalar(
        select(Invitation)
        .options(selectinload(Invitation.assignment).selectinload(Assignment.tutor))
        .where(Invitation.id == invitation.id)
    )
    _send_invitation_email(invitation, request)
    return _invitation_out(invitation)


@router.get("", response_model=list[InvitationOut])
def list_invitations(db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    invitations = db.scalars(
        select(Invitation)
        .options(selectinload(Invitation.assignment).selectinload(Assignment.tutor))
        .order_by(Invitation.created_at.desc())
    ).all()
    return [_invitation_out(invitation) for invitation in invitations]


@router.delete("/{invitation_id}")
def delete_invitation(invitation_id: UUID, db: Session = Depends(get_db), _: User = Depends(require_role("admin_master"))):
    invitation = db.get(Invitation, invitation_id)
    if not invitation:
        raise HTTPException(status_code=404, detail="invitation not found")
    if invitation.accepted_at:
        raise HTTPException(status_code=409, detail="accepted invitation cannot be deleted")
    db.delete(invitation)
    db.commit()
    return {"status": "ok"}
# === Phase 3 END ===
