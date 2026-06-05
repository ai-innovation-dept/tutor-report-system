# === Phase 8: フロントエンド共通 START ===
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.time import get_current_jst_month
from app.database import get_db
from app.deps import get_current_user_from_cookie
from app.models import Assignment, LessonReport, ReportAction, ReportEvent, ReportStatus, User

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
    current_month = get_current_jst_month()
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
    current_month = get_current_jst_month()
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
    current_month = get_current_jst_month()
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


def _teaching_minutes(report: LessonReport) -> int:
    start = report.start_time.hour * 60 + report.start_time.minute
    end = report.end_time.hour * 60 + report.end_time.minute
    return max(0, end - start - (report.break_minutes or 0))


def _group_key(report: LessonReport) -> tuple[str, str]:
    return (str(report.assignment_id), report.target_month)


def _approval_event(events: list[ReportEvent], action: str, latest: bool = True) -> ReportEvent | None:
    matched = [event for event in events if event.action == action]
    if not matched:
        return None
    return sorted(matched, key=lambda event: event.created_at, reverse=latest)[0]


RETURN_ACTION_BY_STEP = {
    "保護者": ReportAction.parent_return.value,
    "受付": ReportAction.return_from_receiver.value,
    "再鑑": ReportAction.return_from_reviewer.value,
    "管理者": ReportAction.return_from_master.value,
}


def _approval_step(label: str, event: ReportEvent | None, return_event: ReportEvent | None = None) -> dict:
    return {
        "label": label,
        "actor_name": event.actor.display_name if event and event.actor else "",
        "created_at": _format_dt(event.created_at) if event else "",
        "returned": return_event is not None,
        "return_actor_name": return_event.actor.display_name if return_event and return_event.actor else "",
        "return_at": _format_dt(return_event.created_at) if return_event else "",
    }


def _approval_groups(reports: list[LessonReport], events_by_report: dict, step_specs: list[tuple[str, str, bool]]) -> list[dict]:
    grouped: dict[tuple[str, str], list[LessonReport]] = {}
    for report in reports:
        grouped.setdefault(_group_key(report), []).append(report)

    groups = []
    for (_, target_month), items in grouped.items():
        items = sorted(items, key=lambda report: (report.lesson_date, report.start_time))
        events = [event for report in items for event in events_by_report.get(report.id, [])]
        first = items[0]
        steps = [
            _approval_step(
                label,
                _approval_event(events, action, latest=latest),
                _approval_event(events, RETURN_ACTION_BY_STEP[label], latest=True) if label in RETURN_ACTION_BY_STEP else None,
            )
            for label, action, latest in step_specs
        ]
        groups.append(
            {
                "assignment_id": str(first.assignment_id),
                "target_month": target_month,
                "student_name": first.assignment.student_name if first.assignment else "生徒未設定",
                "tutor_name": first.tutor.display_name if first.tutor else "講師未設定",
                "parent_name": first.parent.display_name if first.parent else "",
                "total_count": len(items),
                "total_minutes_label": _duration_label(sum(_teaching_minutes(report) for report in items)),
                "current_status": _current_group_status(items),
                "steps": steps,
                "step_map": {step["label"]: step for step in steps},
            }
        )
    return sorted(groups, key=lambda group: (group["target_month"], group["student_name"]), reverse=True)


def _events_by_report(db: Session, reports: list[LessonReport]) -> dict:
    report_ids = [report.id for report in reports]
    if not report_ids:
        return {}
    events = db.scalars(
        select(ReportEvent)
        .options(selectinload(ReportEvent.actor))
        .where(ReportEvent.report_id.in_(report_ids))
        .order_by(ReportEvent.created_at)
    ).all()
    grouped: dict = {}
    for event in events:
        grouped.setdefault(event.report_id, []).append(event)
    return grouped


def _current_group_status(reports: list[LessonReport]) -> str:
    priority = [
        ReportStatus.admin_approved.value,
        ReportStatus.returned_to_tutor.value,
        ReportStatus.returned_to_receiver.value,
        ReportStatus.submitted_to_admin.value,
        ReportStatus.received.value,
        ReportStatus.re_reviewed.value,
    ]
    statuses = {report.status for report in reports}
    for status in priority:
        if status in statuses:
            return status
    return reports[0].status if reports else ""


def _parent_approval_groups(db: Session, current_user: User) -> list[dict]:
    reports = db.scalars(
        select(LessonReport)
        .options(selectinload(LessonReport.assignment), selectinload(LessonReport.tutor), selectinload(LessonReport.parent))
        .where(
            LessonReport.parent_id == current_user.id,
            LessonReport.status.in_(
                [
                    ReportStatus.awaiting_parent_approval.value,
                    ReportStatus.returned_to_tutor.value,
                    ReportStatus.parent_approved.value,
                ]
            ),
        )
        .order_by(LessonReport.target_month.desc(), LessonReport.lesson_date.asc(), LessonReport.start_time.asc())
    ).all()
    return _approval_groups(
        reports,
        _events_by_report(db, reports),
        [
            ("講師から依頼", ReportAction.submit_to_parent.value, False),
            ("保護者 承認", ReportAction.parent_approve.value, True),
        ],
    )


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


@router.get("/select-role", response_class=HTMLResponse)
def select_role_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return _login_redirect()
    return templates.TemplateResponse(request, "select_role.html", context=_base_context(request, user))


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return _login_redirect()
    if user.role == "tutor":
        return RedirectResponse("/tutor/reports")
    if user.role == "parent":
        return RedirectResponse("/parent/approval")
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


# 生徒管理機能は将来リリース予定のため一時非表示
# @router.get("/tutor/students", response_class=HTMLResponse)
# def tutor_students_page(request: Request, db: Session = Depends(get_db)):
#     user = get_current_user_from_cookie(request, db)
#     if not user or user.role != "tutor":
#         return _login_redirect()
#     return templates.TemplateResponse(request, "tutor/students.html", context=_tutor_context(request, db, user))


@router.get("/parent/reports", response_class=HTMLResponse)
@router.get("/parent/reports/{report_id}", response_class=HTMLResponse)
def parent_reports_redirect(request: Request):
    return RedirectResponse("/parent/approval", status_code=301)


@router.get("/parent/approval", response_class=HTMLResponse)
def parent_approval_page(request: Request, db: Session = Depends(get_db)):
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
            "id": str(a.id),
            "student_name": a.student_name,
            "tutor_id": str(a.tutor_id),
            "tutor_name": a.tutor.display_name if a.tutor else "",
        }
        for a in assignments
    ]
    return templates.TemplateResponse(request, "parent/approval.html", context=context)


@router.get("/admin/stale-reports", response_class=HTMLResponse)
def admin_stale_reports_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role not in {"admin_receiver", "admin_reviewer", "admin_master"}:
        return _login_redirect()
    return templates.TemplateResponse(request, "admin/stale_reports.html", context=_base_context(request, user))


@router.get("/admin/dashboard", response_class=HTMLResponse)
@router.get("/admin/queue/receive", response_class=HTMLResponse)
@router.get("/admin/queue/review", response_class=HTMLResponse)
@router.get("/admin/queue/approve", response_class=HTMLResponse)
@router.get("/admin/reports/{report_id}", response_class=HTMLResponse)
@router.get("/admin/users", response_class=HTMLResponse)
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
        },
    }
    if not (path.startswith("/admin/reports/") or path in allowed_paths.get(user.role, set())):
        return _login_redirect()
    context = _base_context(request, user)
    if path == "/admin/users":
        return templates.TemplateResponse(request, "admin/users.html", context=context)
    return templates.TemplateResponse(request, "admin/dashboard.html", context=context)
# === Phase 9 END ===
