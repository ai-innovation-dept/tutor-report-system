"""招待管理 API。経理（admin_master / admin_chief）・営業・事務が操作可能。"""
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.dependencies.auth import has_role, require_role
from app.models.shared import Invitation, User
from app.schemas.invitations import InvitationCreate, InvitationOut
from app.services.mailer import enqueue_mail
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
    "admin_chief":  "【業務連絡表システム】管理責任者アカウントのご案内",
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
    url = f"{base_url}/register?token={inv.token}"
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
    current_user: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    email = str(payload.email).lower()
    if payload.role not in ALLOWED_INVITATION_ROLES:
        raise HTTPException(status_code=422, detail="role is invalid")
    # 管理責任者の招待は管理責任者のみ
    if payload.role == "admin_chief" and not has_role(current_user, "admin_chief"):
        raise HTTPException(status_code=403, detail="管理責任者の招待は管理責任者のみ可能です")
    if payload.display_name is not None and not payload.display_name.strip():
        raise HTTPException(status_code=422, detail="display_name cannot be blank")
    if payload.role == "school" and not payload.display_name:
        raise HTTPException(status_code=422, detail="display_name is required")

    existing_user = get_user_by_email(db, email)
    # 削除済み（ソフトデリート）ユーザーは重複扱いにしない。削除時にメールアドレスは解放済みのため
    # 通常はここに現れず、同じアドレスの招待は新しいアカウントとして登録される（202607210807 ②）。
    if existing_user and not existing_user.deleted_at and "new" in (existing_user.allowed_systems or []):
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

    base_url = settings.NEW_BASE_URL.rstrip("/")
    # 即時送信せず送信キューへ投函（実送信はドレイナが順次・間隔をあけて行う）
    enqueue_mail(db, inv.email, _SUBJECTS.get(inv.role, _SUBJECTS["school"]), _email_body(inv, base_url))
    return _invitation_out(inv)


@router.get("", response_model=list[InvitationOut])
def list_invitations(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    invs = db.scalars(
        select(Invitation)
        .where(Invitation.role.in_(["tutor", "school", "sales", "office", "admin_master", "admin_chief"]))
        .order_by(Invitation.created_at.desc())
    ).all()
    return [_invitation_out(inv) for inv in invs]


@router.delete("/{invitation_id}")
def delete_invitation(
    invitation_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin_master", "admin_chief", "sales", "office")),
):
    inv = db.get(Invitation, invitation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="invitation not found")
    if inv.accepted_at:
        raise HTTPException(status_code=409, detail="accepted invitation cannot be deleted")
    db.delete(inv)
    db.commit()
    return {"status": "ok"}
