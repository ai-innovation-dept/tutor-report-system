import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.shared import User

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["pages"])


def _get_user_optional(request: Request, db: Session) -> User | None:
    token = request.cookies.get("w_access_token")
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload:
        return None
    try:
        user_id = uuid.UUID(payload.get("sub", ""))
    except (ValueError, TypeError):
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def _active_role(request: Request, user: User) -> str:
    roles: list[str] = list(user.roles or []) or ([user.role] if user.role else [])
    selected = request.cookies.get("w_selected_role")
    if selected and selected in roles:
        return selected
    return roles[0] if roles else user.role


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def _ctx(request: Request, user: User) -> dict:
    return {
        "request": request,
        "current_user": user,
        "active_role": _active_role(request, user),
    }


def _roles(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def _require_page_role(request: Request, role_or_roles: str | list[str], db: Session) -> tuple[User | None, RedirectResponse | None]:
    user = _get_user_optional(request, db)
    if not user:
        return None, _login_redirect()
    required = [role_or_roles] if isinstance(role_or_roles, str) else role_or_roles
    if not any(r in _roles(user) for r in required):
        return None, _login_redirect()
    return user, None


# ---------------------------------------------------------------------------
# ルート
# ---------------------------------------------------------------------------

@router.get("/", include_in_schema=False)
def root(request: Request, db: Session = Depends(get_db)):
    user = _get_user_optional(request, db)
    if not user:
        return _login_redirect()
    role = _active_role(request, user)
    destinations = {
        "tutor": "/tutor/reports",
        "school": "/school/approval",
        "sales": "/sales/queue",
        "office": "/office/queue",
        "admin_master": "/finance/queue",
        "admin_chief": "/finance/queue",
    }
    return RedirectResponse(url=destinations.get(role, "/login"), status_code=302)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    user = _get_user_optional(request, db)
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"request": request})


@router.get("/select-role", response_class=HTMLResponse)
def select_role_page(request: Request, db: Session = Depends(get_db)):
    user = _get_user_optional(request, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse(request, "select_role.html", _ctx(request, user))


@router.get("/tutor/reports", response_class=HTMLResponse)
def tutor_reports(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "tutor", db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "tutor/reports.html", _ctx(request, user))


@router.get("/tutor/reports/new", response_class=HTMLResponse)
def tutor_reports_new(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "tutor", db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "tutor/reports.html", _ctx(request, user))


@router.get("/tutor/submit", include_in_schema=False)
def tutor_submit_redirect():
    return RedirectResponse(url="/tutor/reports", status_code=301)


@router.get("/tutor/reports/{report_id}", response_class=HTMLResponse)
def tutor_report_detail(request: Request, report_id: str, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "tutor", db)
    if redirect:
        return redirect
    context = _ctx(request, user)
    context["report_id"] = report_id
    return templates.TemplateResponse(request, "tutor/report_detail.html", context)


@router.get("/tutor/approval", response_class=HTMLResponse)
def tutor_approval(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "tutor", db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "tutor/approval.html", _ctx(request, user))


@router.get("/reports/{report_id}/view", response_class=HTMLResponse)
def report_view(request: Request, report_id: str, db: Session = Depends(get_db)):
    """報告書の参照専用ビュー（別ウィンドウで開く）。ログイン済みなら誰でも閲覧可。"""
    user = _get_user_optional(request, db)
    if not user:
        return _login_redirect()
    context = _ctx(request, user)
    context["report_id"] = report_id
    return templates.TemplateResponse(request, "report_view.html", context)


@router.get("/school/approval", response_class=HTMLResponse)
def school_approval(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "school", db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "school/approval.html", _ctx(request, user))


@router.get("/school/reports", include_in_schema=False)
def school_reports_redirect():
    return RedirectResponse(url="/school/approval", status_code=301)


@router.get("/sales/queue", response_class=HTMLResponse)
def sales_queue(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "sales", db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "sales/queue.html", _ctx(request, user))


@router.get("/office/queue", response_class=HTMLResponse)
def office_queue(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, "office", db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "office/queue.html", _ctx(request, user))


@router.get("/finance/queue", response_class=HTMLResponse)
def finance_queue(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, ["admin_master", "admin_chief"], db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "finance/queue.html", _ctx(request, user))


@router.get("/admin/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, ["admin_master", "admin_chief"], db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "admin/dashboard.html", _ctx(request, user))


@router.get("/admin/reports/{report_id}", response_class=HTMLResponse)
def admin_report_detail(request: Request, report_id: str, db: Session = Depends(get_db)):
    user = _get_user_optional(request, db)
    if not user:
        return _login_redirect()
    if not {"sales", "office", "admin_master", "admin_chief"}.intersection(_roles(user)):
        return _login_redirect()
    context = _ctx(request, user)
    context["report_id"] = report_id
    return templates.TemplateResponse(request, "admin/report_detail.html", context)


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, ["admin_master", "admin_chief"], db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "admin/users.html", _ctx(request, user))


@router.get("/admin/contracts", response_class=HTMLResponse)
def admin_contracts(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, ["admin_master", "admin_chief"], db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "admin/contracts.html", _ctx(request, user))


@router.get("/admin/stale-reports", response_class=HTMLResponse)
def admin_stale_reports(request: Request, db: Session = Depends(get_db)):
    user, redirect = _require_page_role(request, ["admin_master", "admin_chief"], db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "admin/stale_reports.html", _ctx(request, user))


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"request": request})


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"request": request})


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html", {"request": request})
