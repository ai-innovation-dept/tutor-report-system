# === Phase 8: フロントエンド共通 START ===
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user_from_cookie
from app.models import Assignment, LessonReport, ReportEvent, ReportStatus, User

templates = Jinja2Templates(directory="app/templates")
router = APIRouter(tags=["pages"])


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def _duration_label(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    if hours and mins:
        return f"{hours}時間{mins}分"
    if hours:
        return f"{hours}時間"
    return f"{mins}分"


def _tutor_month_total_label(db: Session, current_user: User) -> str:
    current_month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    reports = db.scalars(
        select(LessonReport).where(
            LessonReport.tutor_id == current_user.id,
            LessonReport.target_month == current_month,
        )
    ).all()
    total = 0
    for report in reports:
        start = report.start_time.hour * 60 + report.start_time.minute
        end = report.end_time.hour * 60 + report.end_time.minute
        total += max(0, end - start - (report.break_minutes or 0))
    return _duration_label(total)


def _format_dt(value: datetime) -> str:
    local = value.astimezone(ZoneInfo(settings.timezone)) if value.tzinfo else value.replace(tzinfo=ZoneInfo(settings.timezone))
    return f"{local.year}/{local.month}/{local.day} {local.hour:02d}:{local.minute:02d}"


def _tutor_return_comments(db: Session, current_user: User) -> list[dict[str, str]]:
    current_month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    return_actions = {
        "parent_return",
        "return_from_receiver",
        "return_from_reviewer",
        "return_from_master",
    }
    reports = db.scalars(
        select(LessonReport).where(
            LessonReport.tutor_id == current_user.id,
            LessonReport.target_month == current_month,
            LessonReport.status == ReportStatus.returned_to_tutor.value,
        )
    ).all()
    if not reports:
        return []

    report_by_id = {report.id: report for report in reports}
    events = db.scalars(
        select(ReportEvent)
        .where(
            ReportEvent.report_id.in_(report_by_id.keys()),
            ReportEvent.action.in_(return_actions),
            ReportEvent.comment.is_not(None),
        )
        .order_by(ReportEvent.created_at.desc())
    ).all()
    latest_by_report = {}
    for event in events:
        latest_by_report.setdefault(event.report_id, event)

    comments = []
    for report in sorted(reports, key=lambda item: item.lesson_date, reverse=True):
        event = latest_by_report.get(report.id)
        if not event:
            continue
        comments.append(
            {
                "lesson_date": report.lesson_date.isoformat(),
                "student_name": report.assignment.student_name if report.assignment else "",
                "comment": event.comment or "",
                "actor_name": event.actor.display_name if event.actor else "",
                "created_at": _format_dt(event.created_at),
            }
        )
    return comments


def _tutor_returned_to_tutor_count(db: Session, current_user: User) -> int:
    current_month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    return (
        db.scalar(
            select(func.count(LessonReport.id))
            .where(
                LessonReport.tutor_id == current_user.id,
                LessonReport.target_month == current_month,
                LessonReport.status == ReportStatus.returned_to_tutor.value,
            )
        )
        or 0
    )


def _base_context(request: Request, current_user: User) -> dict:
    return {"request": request, "current_user": current_user}


def _tutor_context(request: Request, db: Session, current_user: User) -> dict:
    context = _base_context(request, current_user)
    context.update(
        {
            "tutor_month_total_label": _tutor_month_total_label(db, current_user),
            "return_comments": _tutor_return_comments(db, current_user),
            "returned_to_tutor_count": _tutor_returned_to_tutor_count(db, current_user),
        }
    )
    return context


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/login")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", context={"request": request})


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", context={"request": request})


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", context={"request": request})


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request):
    return templates.TemplateResponse(request, "reset_password.html", context={"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return _login_redirect()
    if user.role == "tutor":
        return RedirectResponse("/tutor/reports")
    if user.role == "parent":
        return RedirectResponse("/parent/reports")
    if user.role.startswith("admin_"):
        return RedirectResponse("/admin/dashboard")
    return _login_redirect()


@router.get("/tutor/reports", response_class=HTMLResponse)
@router.get("/tutor/reports/new", response_class=HTMLResponse)
@router.get("/tutor/reports/{report_id}", response_class=HTMLResponse)
@router.get("/tutor/submit", response_class=HTMLResponse)
def tutor_pages(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != "tutor":
        return _login_redirect()
    return templates.TemplateResponse(request, "tutor/reports.html", context=_tutor_context(request, db, user))


@router.get("/tutor/approval", response_class=HTMLResponse)
def tutor_approval_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != "tutor":
        return _login_redirect()
    return templates.TemplateResponse(request, "tutor/approval.html", context=_tutor_context(request, db, user))


@router.get("/parent/reports", response_class=HTMLResponse)
@router.get("/parent/reports/{report_id}", response_class=HTMLResponse)
def parent_pages(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != "parent":
        return _login_redirect()
    assignments = db.scalars(
        select(Assignment)
        .where(Assignment.parent_id == user.id, Assignment.is_active.is_(True))
        .order_by(Assignment.student_name)
    ).all()
    context = _base_context(request, user)
    context["assignments"] = [
        {
            "id": str(assignment.id),
            "student_name": assignment.student_name,
            "tutor_id": str(assignment.tutor_id),
            "tutor_name": assignment.tutor.display_name if assignment.tutor else "",
        }
        for assignment in assignments
    ]
    return templates.TemplateResponse(request, "parent/reports.html", context=context)


@router.get("/admin/dashboard", response_class=HTMLResponse)
@router.get("/admin/queue/receive", response_class=HTMLResponse)
@router.get("/admin/queue/review", response_class=HTMLResponse)
@router.get("/admin/queue/approve", response_class=HTMLResponse)
@router.get("/admin/reports/{report_id}", response_class=HTMLResponse)
@router.get("/admin/users", response_class=HTMLResponse)
@router.get("/admin/assignments", response_class=HTMLResponse)
def admin_pages(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or not user.role.startswith("admin_"):
        return _login_redirect()
    path = request.url.path
    allowed_paths = {
        "admin_receiver": {"/admin/dashboard", "/admin/queue/receive"},
        "admin_reviewer": {"/admin/dashboard", "/admin/queue/review"},
        "admin_master": {
            "/admin/dashboard",
            "/admin/queue/receive",
            "/admin/queue/review",
            "/admin/queue/approve",
            "/admin/users",
            "/admin/assignments",
        },
    }
    if not (path.startswith("/admin/reports/") or path in allowed_paths.get(user.role, set())):
        return _login_redirect()
    context = _base_context(request, user)
    if path == "/admin/users":
        return templates.TemplateResponse(request, "admin/users.html", context=context)
    if path == "/admin/assignments":
        return templates.TemplateResponse(request, "admin/assignments.html", context=context)
    return templates.TemplateResponse(request, "admin/dashboard.html", context=context)
# === Phase 9 END ===
