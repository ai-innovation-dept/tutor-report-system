# === Phase 2: 認証・認可 START ===
from uuid import UUID
from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models import LessonReport, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(token: str | None = Depends(oauth2_scheme), access_token: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    raw_token = token or access_token
    subject = decode_access_token(raw_token) if raw_token else None
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    user = db.get(User, UUID(subject))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="inactive user")
    return user


def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    subject = decode_access_token(token)
    if not subject:
        return None
    try:
        user_id = UUID(subject)
    except ValueError:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def can_view_report(user: User, report: LessonReport) -> bool:
    if user.role.startswith("admin_"):
        return True
    return (user.role == "tutor" and report.tutor_id == user.id) or (user.role == "parent" and report.parent_id == user.id)


def get_report_for_user(report_id: UUID, user: User, db: Session) -> LessonReport:
    report = db.scalar(select(LessonReport).where(LessonReport.id == report_id))
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    if not can_view_report(user, report):
        raise HTTPException(status_code=403, detail="report access denied")
    return report
# === Phase 4 END ===
