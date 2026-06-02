import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.models.shared import User

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["pages"])


def _get_db() -> Session:
    db = SessionLocal()
    try:
        return db
    finally:
        pass


def _get_user_optional(request: Request) -> User | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    try:
        user_id = uuid.UUID(payload.get("sub", ""))
    except (ValueError, TypeError):
        return None
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if not user or not user.is_active:
            return None
        # セッション外でも属性が使えるよう値を確定させる
        _ = user.id, user.email, user.role, user.roles, user.display_name, user.tutor_no, user.user_no
        return user
    finally:
        db.close()


def _active_role(request: Request, user: User) -> str:
    roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
    selected = request.cookies.get("selected_role")
    if selected and selected in roles:
        return selected
    return roles[0] if roles else user.role


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/w/login", status_code=302)


def _ctx(request: Request, user: User) -> dict:
    return {
        "request": request,
        "current_user": user,
        "active_role": _active_role(request, user),
    }


# ---------------------------------------------------------------------------
# ルート
# ---------------------------------------------------------------------------

@router.get("/w/", include_in_schema=False)
def root(request: Request):
    user = _get_user_optional(request)
    if not user:
        return _login_redirect()
    role = _active_role(request, user)
    destinations = {
        "tutor": "/w/tutor/reports",
        "school": "/w/school/approval",
        "sales": "/w/sales/approval",
        "office": "/w/office/approval",
        "admin_master": "/w/admin/dashboard",
    }
    return RedirectResponse(url=destinations.get(role, "/w/login"), status_code=302)


@router.get("/w/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = _get_user_optional(request)
    if user:
        return RedirectResponse(url="/w/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"request": request})


@router.get("/w/tutor/reports", response_class=HTMLResponse)
def tutor_reports(request: Request):
    user = _get_user_optional(request)
    if not user:
        return _login_redirect()
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "tutor" not in roles:
        return _login_redirect()
    return templates.TemplateResponse(request, "tutor/reports.html", _ctx(request, user))


@router.get("/w/school/approval", response_class=HTMLResponse)
def school_approval(request: Request):
    user = _get_user_optional(request)
    if not user:
        return _login_redirect()
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "school" not in roles:
        return _login_redirect()
    return templates.TemplateResponse(request, "school/approval.html", _ctx(request, user))


@router.get("/w/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    user = _get_user_optional(request)
    if not user:
        return _login_redirect()
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "admin_master" not in roles:
        return _login_redirect()
    return templates.TemplateResponse(request, "admin/dashboard.html", _ctx(request, user))
