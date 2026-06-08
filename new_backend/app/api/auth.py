import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.dependencies.auth import get_current_user, get_active_role
from app.models.shared import Invitation, PasswordResetToken, User
from app.schemas.auth import (
    ForgotPasswordIn,
    LoginRequest,
    RegisterIn,
    RegisterInfoOut,
    RegisterOut,
    ResetPasswordIn,
    ResetTokenInfoOut,
    RoleSelectRequest,
    TokenResponse,
)
from app.schemas.users import UserOut
from app.services.notification_service import send_email
from app.services.user_service import (
    ROLE_LABELS,
    allowed_systems_for_role,
    authenticate,
    effective_roles,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

ROLE_LABELS = {
    "tutor": "講師",
    "school": "学校担当",
    "sales": "営業",
    "office": "事務",
    "admin_master": "管理者",
}

_DASHBOARD = {
    "tutor": "/tutor/reports",
    "school": "/school/approval",
    "sales": "/sales/queue",
    "office": "/office/queue",
    "admin_master": "/admin/dashboard",
}


def _dashboard(role: str) -> str:
    return _DASHBOARD.get(role, "/")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, str(payload.username), payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    # 所属チェック: 新システム(new)に登録のないユーザーはログイン不可（招待による登録が必要）。
    if "new" not in (user.allowed_systems or []):
        raise HTTPException(
            status_code=403,
            detail="このシステムには登録がありません。ご利用には管理者の招待が必要です。",
        )

    roles = effective_roles(user)
    token = create_access_token({"sub": str(user.id)})
    # 既存システム（指導実績報告システム）とクッキーを共有しないよう、新システム専用名を使う。
    # これにより別システムへ同一ブラウザでアクセスしても自動ログインされず、各システムで個別ログインが必要になる。
    response.set_cookie(key="w_access_token", value=token, httponly=True, samesite="lax")

    if len(roles) == 1:
        response.set_cookie(key="w_selected_role", value=roles[0], httponly=True, samesite="lax")
        return TokenResponse(
            access_token=token,
            role=roles[0],
            roles=roles,
            redirect_url=_dashboard(roles[0]),
        )
    return TokenResponse(access_token=token, roles=roles, redirect_url="/select-role")


@router.post("/select-role", response_model=TokenResponse)
def select_role(
    payload: RoleSelectRequest,
    response: Response,
    user=Depends(get_current_user),
):
    roles = effective_roles(user)
    if payload.role not in roles:
        raise HTTPException(status_code=403, detail="role not available")
    response.set_cookie(key="w_selected_role", value=payload.role, httponly=True, samesite="lax")
    return TokenResponse(
        access_token="",
        role=payload.role,
        roles=roles,
        redirect_url=_dashboard(payload.role),
    )


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("w_access_token")
    response.delete_cookie("w_selected_role")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user=Depends(get_current_user)):
    return user


# ---------------------------------------------------------------------------
# 登録・パスワードリセット（/api/auth/* 公開エンドポイント）
# ---------------------------------------------------------------------------

def _valid_invitation(token: str, db: Session) -> Invitation:
    inv = db.scalar(select(Invitation).where(Invitation.token == token))
    if not inv:
        raise HTTPException(status_code=404, detail="招待が無効です")
    now = datetime.now(timezone.utc)
    expires = inv.expires_at if inv.expires_at.tzinfo else inv.expires_at.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(status_code=410, detail="招待の有効期限が切れています")
    if inv.accepted_at:
        raise HTTPException(status_code=409, detail="この招待は使用済みです")
    return inv


def _reset_token_status(token_obj: PasswordResetToken | None) -> str | None:
    if not token_obj:
        return "not_found"
    if token_obj.used_at:
        return "used"
    expires = token_obj.expires_at if token_obj.expires_at.tzinfo else token_obj.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        return "expired"
    return None


@router.get("/register", response_model=RegisterInfoOut)
def register_info(token: str, db: Session = Depends(get_db)):
    inv = _valid_invitation(token, db)
    return RegisterInfoOut(
        email=inv.email,
        role=inv.role,
        role_display=ROLE_LABELS.get(inv.role, inv.role),
        display_name=inv.display_name,
        user_no=inv.tutor_no,
    )


@router.post("/register", response_model=RegisterOut)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    inv = _valid_invitation(payload.token, db)
    existing_user = db.scalar(select(User).where(User.email == inv.email))

    display_name = (payload.display_name or inv.display_name or inv.email.split("@", 1)[0]).strip()
    if not display_name:
        raise HTTPException(status_code=422, detail="display_name is required")

    user_no = inv.tutor_no  # generate_user_no の結果が格納済み
    if existing_user:
        systems = list(existing_user.allowed_systems or [])
        if "new" not in systems:
            systems.append("new")
        # admin_master は常に両システム。
        if inv.role == "admin_master" and "legacy" not in systems:
            systems.append("legacy")
        existing_user.allowed_systems = systems
        roles = list(existing_user.roles or []) or ([existing_user.role] if existing_user.role else [])
        if inv.role not in roles:
            roles.append(inv.role)
        existing_user.roles = roles
        if not existing_user.role:
            existing_user.role = inv.role
        if not existing_user.user_no:
            existing_user.user_no = user_no
        if inv.role == "tutor" and not existing_user.tutor_no:
            existing_user.tutor_no = user_no
        inv.accepted_at = datetime.now(timezone.utc)
        db.commit()
        return RegisterOut(message="registered")

    user = User(
        email=inv.email,
        role=inv.role,
        roles=[inv.role],
        display_name=display_name,
        user_no=user_no,
        tutor_no=user_no if inv.role == "tutor" else None,  # legacy 互換
        # admin_master は常に両システム、それ以外は当(new)システムのみ。
        allowed_systems=allowed_systems_for_role(inv.role),
        password_hash=hash_password(payload.password),
        is_active=True,
    )
    db.add(user)
    inv.accepted_at = datetime.now(timezone.utc)
    db.commit()
    return RegisterOut(message="registered")


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    email = str(payload.email).strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    # メール存在を明かさない
    if not user:
        return {"message": "パスワードリセットメールを送信しました"}
    token_obj = PasswordResetToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(token_obj)
    db.commit()

    base_url = settings.BASE_URL.rstrip("/")
    body = (
        f"{user.display_name} 様\n\n"
        f"パスワードリセットのリクエストを受け付けました。\n"
        f"以下のURLから新しいパスワードを設定してください。\n\n"
        f"【パスワードリセットURL】\n{base_url}/reset-password?token={token_obj.token}\n\n"
        f"このURLの有効期限は1時間です\n"
        f"このメールに心当たりがない場合は無視してください\n"
    )
    background_tasks.add_task(
        send_email,
        user.email,
        "【業務連絡表システム】パスワードリセットのご案内",
        body,
    )
    return {"message": "パスワードリセットメールを送信しました"}


@router.get("/reset-password", response_model=ResetTokenInfoOut)
def reset_password_info(token: str, db: Session = Depends(get_db)):
    token_obj = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == token))
    reason = _reset_token_status(token_obj)
    if reason:
        return ResetTokenInfoOut(valid=False, reason=reason)
    return ResetTokenInfoOut(valid=True, email=token_obj.user.email)


@router.post("/reset-password", response_model=RegisterOut)
def reset_password(payload: ResetPasswordIn, db: Session = Depends(get_db)):
    token_obj = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token == payload.token))
    reason = _reset_token_status(token_obj)
    if reason == "not_found":
        raise HTTPException(status_code=404, detail="token not found")
    if reason == "expired":
        raise HTTPException(status_code=410, detail="リンクの有効期限が切れています")
    if reason == "used":
        raise HTTPException(status_code=409, detail="このリンクは使用済みです")
    token_obj.user.password_hash = hash_password(payload.new_password)
    token_obj.used_at = datetime.now(timezone.utc)
    db.commit()
    return RegisterOut(message="パスワードを変更しました")
