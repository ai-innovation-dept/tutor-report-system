# === Phase 2: 認証・認可 START ===
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import authenticate_user, create_access_token, hash_password
from app.database import get_db
from app.deps import get_current_user
from app.models import Assignment, Invitation, LessonReport, User
from app.schemas import RegisterIn, RegisterInfoOut, TokenOut, UserOut


ROLE_LABELS = {
    "parent": "保護者",
    "tutor": "講師",
    "admin_receiver": "受付担当",
    "admin_reviewer": "再鑑者",
    "admin_master": "管理者",
}

router = APIRouter(prefix="/api/auth", tags=["auth"])


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


@router.post("/register", response_model=TokenOut)
def register_parent(payload: RegisterIn, response: Response, db: Session = Depends(get_db)):
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
    access_token = create_access_token(str(user.id))
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
    )
    return TokenOut(access_token=access_token)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
# === Phase 2 END ===
