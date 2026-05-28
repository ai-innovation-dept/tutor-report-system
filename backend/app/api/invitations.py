# === Phase 3: 招待管理 START ===
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
TUTOR_INVITATION_SUBJECT = "【指導実績報告システム】講師アカウントのご案内"
STAFF_INVITATION_SUBJECT = "【指導実績報告システム】スタッフアカウントのご案内"
ALLOWED_INVITATION_ROLES = {"parent", "tutor", "admin_receiver", "admin_reviewer", "admin_master"}
ROLE_LABELS = {
    "parent": "保護者",
    "tutor": "講師",
    "admin_receiver": "受付担当",
    "admin_reviewer": "再鑑者",
    "admin_master": "管理者",
}


def _invitation_out(invitation: Invitation, message: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        assignment_id=invitation.assignment_id,
        tutor_id=invitation.assignment.tutor_id if invitation.assignment else None,
        tutor_name=invitation.assignment.tutor.display_name if invitation.assignment and invitation.assignment.tutor else None,
        display_name=invitation.display_name,
        tutor_no=invitation.tutor_no,
        student_name=invitation.assignment.student_name if invitation.assignment else None,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        created_at=invitation.created_at,
        message=message,
    )


def _base_url(request: Request) -> str:
    return settings.base_url.rstrip("/") or str(request.base_url).rstrip("/")


def _invitation_body(invitation: Invitation, request: Request) -> str:
    if invitation.role == "tutor":
        template = Path("app/templates/email/invitation_tutor.txt").read_text(encoding="utf-8")
        return template.format(
            display_name=invitation.display_name or invitation.email.split("@", 1)[0],
            base_url=_base_url(request),
            token=invitation.token,
            tutor_no=invitation.tutor_no or "",
        )
    if invitation.role.startswith("admin_"):
        template = Path("app/templates/email/invitation_staff.txt").read_text(encoding="utf-8")
        return template.format(
            display_name=invitation.display_name or invitation.email.split("@", 1)[0],
            base_url=_base_url(request),
            token=invitation.token,
            role_display=ROLE_LABELS.get(invitation.role, invitation.role),
        )
    template = Path("app/templates/email/invitation.txt").read_text(encoding="utf-8")
    return template.format(
        display_name="保護者",
        base_url=_base_url(request),
        token=invitation.token,
        tutor_name=invitation.assignment.tutor.display_name if invitation.assignment and invitation.assignment.tutor else "未設定",
        student_name=invitation.assignment.student_name if invitation.assignment else "未設定",
    )


async def _send_invitation_email(invitation: Invitation, request: Request) -> None:
    subject = INVITATION_SUBJECT
    if invitation.role == "tutor":
        subject = TUTOR_INVITATION_SUBJECT
    elif invitation.role.startswith("admin_"):
        subject = STAFF_INVITATION_SUBJECT
    await EmailChannel().send(invitation.email, subject, _invitation_body(invitation, request))


def generate_tutor_no(db: Session) -> str:
    tutors = db.scalars(select(User.tutor_no).where(User.role == "tutor", User.tutor_no.is_not(None))).all()
    invitations = db.scalars(select(Invitation.tutor_no).where(Invitation.role == "tutor", Invitation.tutor_no.is_not(None), Invitation.accepted_at.is_(None))).all()
    max_no = 0
    for tutor_no in [*tutors, *invitations]:
        try:
            max_no = max(max_no, int(str(tutor_no).replace("T", "")))
        except ValueError:
            continue
    return f"T{max_no + 1:03d}"


def _validate_invitation_payload(payload: InvitationCreate) -> None:
    if payload.role not in ALLOWED_INVITATION_ROLES:
        raise HTTPException(status_code=422, detail="role is invalid")
    if payload.role == "parent":
        if not payload.tutor_id:
            raise HTTPException(status_code=422, detail="tutor_id is required for parent invitations")
        if not payload.student_name or not payload.student_name.strip():
            raise HTTPException(status_code=422, detail="student_name is required")
    if payload.role != "parent" and payload.display_name is not None and not payload.display_name.strip():
        raise HTTPException(status_code=422, detail="display_name cannot be blank")


def _assignment_for_parent_payload(payload: InvitationCreate, db: Session) -> Assignment:
    assert payload.tutor_id is not None
    assert payload.student_name is not None
    tutor = db.get(User, payload.tutor_id)
    if not tutor or tutor.role != "tutor":
        raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
    student_name = payload.student_name.strip()
    if payload.assignment_id:
        assignment = db.get(Assignment, payload.assignment_id)
        if not assignment:
            raise HTTPException(status_code=404, detail="assignment not found")
        assignment.tutor_id = payload.tutor_id
        assignment.student_name = student_name
        assignment.is_active = True
        return assignment
    assignment = Assignment(tutor_id=payload.tutor_id, student_name=student_name, parent_id=None, is_active=True)
    db.add(assignment)
    db.flush()
    return assignment


def prepare_parent_invitation_for_assignment(
    email: str,
    assignment: Assignment,
    db: Session,
    invited_by: User,
) -> tuple[Invitation, str, bool]:
    email = email.lower()
    now = datetime.now(timezone.utc)
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and existing_user.role != "parent":
        raise HTTPException(status_code=409, detail="このメールアドレスは登録済みです")
    if existing_user:
        assignment.parent_id = existing_user.id
        invitation = Invitation(
            email=email,
            role="parent",
            assignment_id=assignment.id,
            token=secrets.token_urlsafe(32),
            invited_by=invited_by.id,
            expires_at=now + timedelta(hours=72),
            accepted_at=now,
            created_at=now,
        )
        db.add(invitation)
        db.flush()
        return invitation, "既存の保護者アカウントに生徒を紐付けました", False

    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.email == email, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    if invitation:
        invitation.assignment_id = assignment.id
        invitation.role = "parent"
        invitation.display_name = None
        invitation.tutor_no = None
        invitation.token = secrets.token_urlsafe(32)
        invitation.invited_by = invited_by.id
        invitation.expires_at = now + timedelta(hours=72)
        invitation.created_at = now
    else:
        invitation = Invitation(
            email=email,
            role="parent",
            assignment_id=assignment.id,
            token=secrets.token_urlsafe(32),
            invited_by=invited_by.id,
            expires_at=now + timedelta(hours=72),
            created_at=now,
        )
        db.add(invitation)
    db.flush()
    return invitation, "保護者へ招待メールを送信しました", True


@router.post("", response_model=InvitationOut)
async def create_invitation(
    payload: InvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin_master")),
):
    email = str(payload.email).lower()
    _validate_invitation_payload(payload)

    now = datetime.now(timezone.utc)
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and not (payload.role == "parent" and existing_user.role == "parent"):
        raise HTTPException(status_code=409, detail="このメールアドレスは登録済みです")
    if existing_user:
        assignment = _assignment_for_parent_payload(payload, db)
        assignment.parent_id = existing_user.id
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
    display_name = payload.display_name.strip() if payload.display_name else None
    tutor_no = generate_tutor_no(db) if payload.role == "tutor" else None
    if invitation:
        if payload.role == "parent":
            if payload.assignment_id:
                assignment = _assignment_for_parent_payload(payload, db)
            elif invitation.assignment and invitation.assignment.parent_id is None:
                assignment = invitation.assignment
                assert payload.tutor_id is not None
                assert payload.student_name is not None
                tutor = db.get(User, payload.tutor_id)
                if not tutor or tutor.role != "tutor":
                    raise HTTPException(status_code=422, detail="tutor_id must be a tutor user")
                assignment.tutor_id = payload.tutor_id
                assignment.student_name = payload.student_name.strip()
                assignment.parent_id = None
                assignment.is_active = True
            else:
                assignment = _assignment_for_parent_payload(payload, db)
        else:
            assignment = None
        invitation.assignment_id = assignment.id if assignment else None
        invitation.role = payload.role
        invitation.display_name = display_name
        invitation.tutor_no = tutor_no
        invitation.token = secrets.token_urlsafe(32)
        invitation.invited_by = user.id
        invitation.expires_at = now + timedelta(hours=72)
        invitation.created_at = now
    else:
        if payload.role == "parent":
            assignment = _assignment_for_parent_payload(payload, db)
        invitation = Invitation(
            email=email,
            role=payload.role,
            display_name=display_name,
            tutor_no=tutor_no,
            assignment_id=assignment.id if assignment else None,
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
    await _send_invitation_email(invitation, request)
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
