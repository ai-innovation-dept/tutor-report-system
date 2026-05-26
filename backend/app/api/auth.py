# === Phase 2: 認証・認可 START ===
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import authenticate_user, create_access_token, hash_password
from app.database import get_db
from app.deps import get_current_user
from app.config import settings
from app.models import Assignment, Invitation, LessonReport, PasswordResetToken, User
from app.schemas import ForgotPasswordIn, RegisterIn, RegisterInfoOut, RegisterOut, ResetPasswordIn, ResetTokenInfoOut, TokenOut, UserOut
from app.services.notification_service import send_email_notification


ROLE_LABELS = {
    "parent": "保護者",
    "tutor": "講師",
    "admin_receiver": "受付担当",
    "admin_reviewer": "再鑑者",
    "admin_master": "管理者",
}

router = APIRouter(prefix="/api/auth", tags=["auth"])
PASSWORD_RESET_SUBJECT = "【指導実績報告システム】パスワードリセットのご案内"


def _valid_invitation(token: str, db: Session) -> Invitation:
    invitation = db.scalar(select(Invitation).where(Invitation.token == token))
    if not invitation:
        raise HTTPException(status_code=404, detail="招待が無効です")
    now = datetime.now(timezone.utc)
    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < now:
        raise HTTPException(status_code=410, detail="招待の有効期限が切れています")
    if invitation.accepted_at:
        raise HTTPException(status_code=409, detail="この招待は使用済みです")
    return invitation


def _as_aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reset_token_status(reset_token: PasswordResetToken | None) -> str | None:
    if not reset_token:
        return "not_found"
    if reset_token.used_at:
        return "used"
    if _as_aware_utc(reset_token.expires_at) < datetime.now(timezone.utc):
        return "expired"
    return None


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="メールアドレスまたはパスワードが違います")
    access_token = create_access_token(str(user.id))
    response = JSONResponse(
        content={
            "access_token": access_token,
            "token_type": "bearer",
            "role": user.role,
            "display_name": user.display_name,
        }
    )
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout():
    response = JSONResponse(content={"message": "logged out"})
    response.delete_cookie("access_token")
    return response


@router.post("/forgot-password")
async def forgot_password(payload: ForgotPasswordIn, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    message = {"message": "パスワードリセットメールを送信しました"}
    if not user:
        return message
    reset_token = PasswordResetToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(reset_token)
    db.commit()
    await send_email_notification(
        user.email,
        PASSWORD_RESET_SUBJECT,
        "password_reset.txt",
        {
            "display_name": user.display_name,
            "base_url": settings.base_url.rstrip("/"),
            "token": reset_token.token,
        },
    )
    return message


@router.get("/reset-password", response_model=ResetTokenInfoOut)
def reset_password_info(token: str, db: Session = Depends(get_db)):
    reset_token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == token))
    reason = _reset_token_status(reset_token)
    if reason:
        return ResetTokenInfoOut(valid=False, reason=reason)
    return ResetTokenInfoOut(valid=True, email=reset_token.user.email)


@router.post("/reset-password", response_model=RegisterOut)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    reset_token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == payload.token))
    reason = _reset_token_status(reset_token)
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="token not found")
    if reason == "expired":
        raise HTTPException(status_code=410, detail="リンクの有効期限が切れています")
    if reason == "used":
        raise HTTPException(status_code=409, detail="このリンクは使用済みです")
    reset_token.user.password_hash = hash_password(payload.new_password)
    reset_token.used_at = datetime.now(timezone.utc)
    db.commit()
    return RegisterOut(message="パスワードを変更しました")


@router.get("/register", response_model=RegisterInfoOut)
def register_info(token: str, db: Session = Depends(get_db)):
    invitation = _valid_invitation(token, db)
    assignment = db.get(Assignment, invitation.assignment_id) if invitation.assignment_id else None
    return RegisterInfoOut(
        email=invitation.email,
        role=invitation.role,
        role_display=ROLE_LABELS.get(invitation.role, invitation.role),
        display_name=invitation.display_name,
        tutor_no=invitation.tutor_no,
        student_name=assignment.student_name if assignment else None,
    )


@router.post("/register", response_model=RegisterOut)
def register_parent(payload: RegisterIn, db: Session = Depends(get_db)):
    invitation = _valid_invitation(payload.token, db)
    if db.scalar(select(User).where(User.email == invitation.email)):
        raise HTTPException(status_code=409, detail="email already exists")
    assignment = db.get(Assignment, invitation.assignment_id) if invitation.assignment_id else None
    if invitation.role == "parent":
        display_name = f"{assignment.student_name}の保護者" if assignment else invitation.email.split("@", 1)[0]
    elif invitation.role == "tutor":
        if not invitation.tutor_no:
            raise HTTPException(status_code=422, detail="tutor_no is required")
        display_name = (payload.display_name or invitation.display_name or invitation.email.split("@", 1)[0]).strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="display_name is required")
    elif invitation.role in {"admin_receiver", "admin_reviewer", "admin_master"}:
        display_name = (payload.display_name or invitation.display_name or invitation.email.split("@", 1)[0]).strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="display_name is required")
    else:
        raise HTTPException(status_code=422, detail="role is invalid")
    user = User(
        email=invitation.email,
        role=invitation.role,
        display_name=display_name,
        tutor_no=invitation.tutor_no if invitation.role == "tutor" else None,
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    db.flush()
    if invitation.role == "parent" and assignment:
        assignment.parent_id = user.id
        db.query(LessonReport).filter(LessonReport.assignment_id == assignment.id).update({"parent_id": user.id}, synchronize_session=False)
    invitation.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return RegisterOut(message="registered")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
# === Phase 2 END ===
