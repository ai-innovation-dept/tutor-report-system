"""招待管理 API。admin_master のみ操作可能。"""
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.dependencies.auth import require_role
from app.models.shared import Invitation, User
from app.schemas.invitations import InvitationCreate, InvitationOut
from app.services.notification_service import send_email
from app.services.user_service import (
    ALLOWED_INVITATION_ROLES,
    ROLE_LABELS,
    generate_user_no,
    get_user_by_email,
)

router = APIRouter(prefix="/api/w/invitations", tags=["work-invitations"])

_SUBJECTS = {
    "tutor":  "【業務連絡表システム】講師アカウントのご案内",
    "school": "【業務連絡表システム】アカウントのご案内",
    "sales":  "【業務連絡表システム】アカウントのご案内",
    "office": "【業務連絡表システム】アカウントのご案内",
    "admin_master": "【業務連絡表システム】管理者アカウントのご案内",
}


def _invitation_out(inv: Invitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        role=inv.role,
        display_name=inv.display_name,
        user_no=inv.tutor_no,           # tutor_noカラムをuser_noとして返す
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        created_at=inv.created_at,
    )


def _email_body(inv: Invitation, base_url: str) -> str:
    name = inv.display_name or inv.email.split("@", 1)[0]
    role_display = ROLE_LABELS.get(inv.role, inv.role)
    user_no = inv.tutor_no or ""
    url = f"{base_url}/w/register?token={inv.token}"
    if inv.role == "tutor":
        return (
            f"{name} 様\n\n"
            f"業務連絡表システムへ講師として招待されました。\n"
            f"以下のURLからアカウントを設定してください。\n\n"
            f"【登録URL】\n{url}\n\n"
            f"このURLの有効期限は72時間です\n"
            f"講師No：{user_no}\n"
        )
    return (
        f"{name} 様\n\n"
        f"業務連絡表システムへ {role_display} として招待されました。\n"
        f"以下のURLからアカウントを設定してください。\n\n"
        f"【登録URL】\n{url}\n\n"
        f"このURLの有効期限は72時間です\n"
        f"ロール：{role_display}  No：{user_no}\n"
    )


@router.post("", response_model=InvitationOut, status_code=201)
async def create_invitation(
    payload: InvitationCreate,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    email = str(payload.email).lower()
    if payload.role not in ALLOWED_INVITATION_ROLES:
        raise HTTPException(status_code=422, detail="role is invalid")
    if payload.display_name is not None and not payload.display_name.strip():
        raise HTTPException(status_code=422, detail="display_name cannot be blank")
    if payload.role == "school" and not payload.display_name:
        raise HTTPException(status_code=422, detail="display_name is required")

    existing_user = get_user_by_email(db, email)
    if existing_user and "new" in (existing_user.allowed_systems or []):
        raise HTTPException(status_code=409, detail="このメールアドレスは登録済みです")

    now = datetime.now(timezone.utc)
    user_no = generate_user_no(db, payload.role)
    display_name = payload.display_name.strip() if payload.display_name else None

    # 同メールの未受諾招待があれば上書き（再送）
    existing = db.scalar(
        select(Invitation).where(Invitation.email == email, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    if existing:
        existing.role = payload.role
        existing.display_name = display_name
        existing.tutor_no = user_no
        existing.token = secrets.token_urlsafe(32)
        existing.expires_at = now + timedelta(hours=72)
        existing.created_at = now
        inv = existing
    else:
        inv = Invitation(
            email=email,
            role=payload.role,
            display_name=display_name,
            tutor_no=user_no,
            token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(hours=72),
            created_at=now,
        )
        db.add(inv)
    db.commit()
    db.refresh(inv)

    base_url = settings.BASE_URL.rstrip("/")
    await send_email(inv.email, _SUBJECTS.get(inv.role, _SUBJECTS["school"]), _email_body(inv, base_url))
    return _invitation_out(inv)


@router.get("", response_model=list[InvitationOut])
def list_invitations(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    invs = db.scalars(
        select(Invitation)
        .where(Invitation.role.in_(["tutor", "school", "sales", "office", "admin_master"]))
        .order_by(Invitation.created_at.desc())
    ).all()
    return [_invitation_out(inv) for inv in invs]


@router.delete("/{invitation_id}")
def delete_invitation(
    invitation_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master")),
):
    inv = db.get(Invitation, invitation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="invitation not found")
    if inv.accepted_at:
        raise HTTPException(status_code=409, detail="accepted invitation cannot be deleted")
    db.delete(inv)
    db.commit()
    return {"status": "ok"}
