import calendar
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ModuleNotFoundError:  # pragma: no cover - fallback for already-built local images
    class BackgroundScheduler:  # type: ignore[no-redef]
        def __init__(self, timezone=None):
            self.timezone = timezone
            self.jobs = []

        def add_job(self, func, trigger, **kwargs):
            self.jobs.append((func, trigger, kwargs))

        def start(self):
            return None

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.database import SessionLocal
from app.dependencies.auth import has_role
from app.models.shared import Assignment, User
from app.models.work import WorkNotification, WorkReport
from app.services.notification_service import record_notification
from app.workflow.definitions import WorkStatus


def _current_jst_date() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


def _to_jst_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(settings.TIMEZONE)).date()


def is_reminder_day(today: date, days_before_month_end: int) -> bool:
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.day >= last_day - days_before_month_end


def enqueue_month_end_reminders(db: Session, today: date | None = None) -> int:
    today = today or _current_jst_date()
    if not is_reminder_day(today, settings.REMINDER_DAYS_BEFORE_MONTH_END):
        return 0
    count = 0
    rows = db.scalars(
        select(WorkReport)
        .options(selectinload(WorkReport.assignment), selectinload(WorkReport.tutor))
        .where(WorkReport.target_month == today.strftime("%Y-%m"))
    ).all()
    for report in rows:
        assignment = report.assignment
        if report.status == WorkStatus.AWAITING_SCHOOL:
            if assignment and assignment.parent:
                record_notification(
                    db,
                    assignment.parent,
                    report,
                    "reminder_unapproved",
                    "Report approval reminder",
                    "Please approve or return the pending report.",
                )
                count += 1
        elif report.status in {WorkStatus.DRAFT, WorkStatus.RETURNED_TO_TUTOR}:
            tutor = report.tutor or db.get(User, report.tutor_id)
            if tutor:
                record_notification(
                    db,
                    tutor,
                    report,
                    "reminder_unsubmitted",
                    "Report submission reminder",
                    "Please complete and submit the report.",
                )
                count += 1
    if count:
        db.flush()
    return count


def enqueue_school_approval_reminders(db: Session, today: date | None = None) -> int:
    """紐付けごとのリマインド設定に基づき、学校確認待ちの報告へ承認督促を送る。

    提出から reminder_days_after 日ごとに、最大 reminder_count 回まで
    学校ユーザー（assignment.parent）へ通知する。
    """
    today = today or _current_jst_date()
    count = 0
    rows = db.scalars(
        select(WorkReport)
        .options(selectinload(WorkReport.assignment))
        .where(WorkReport.status == WorkStatus.AWAITING_SCHOOL)
    ).all()
    for report in rows:
        assignment = report.assignment
        if (
            not assignment
            or not assignment.reminder_enabled
            or not assignment.parent_id
            or assignment.skip_parent_approval
            or not report.submitted_at
        ):
            continue
        school = db.get(User, assignment.parent_id)
        if not school or not school.is_active:
            continue

        sent = db.scalars(
            select(WorkNotification).where(
                WorkNotification.report_id == report.id,
                WorkNotification.type == "reminder_school_approval",
            )
        ).all()
        if len(sent) >= max(1, assignment.reminder_count):
            continue
        if any(n.created_at and _to_jst_date(n.created_at) == today for n in sent):
            continue  # 同日二重送信を防ぐ

        interval = max(1, assignment.reminder_days_after)
        next_due = _to_jst_date(report.submitted_at) + timedelta(days=interval * (len(sent) + 1))
        if today < next_due:
            continue

        record_notification(
            db,
            school,
            report,
            "reminder_school_approval",
            "【業務連絡表】承認のお願い（リマインド）",
            f"{assignment.student_name}の{report.target_month}分の業務連絡表が承認待ちです。ご確認をお願いします。",
        )
        count += 1
    if count:
        db.flush()
    return count


def _stale_reports(db: Session) -> list[WorkReport]:
    return list(
        db.scalars(
            select(WorkReport)
            .options(selectinload(WorkReport.assignment))
            .where(WorkReport.target_month < _current_jst_date().strftime("%Y-%m"))
            .where(WorkReport.status.notin_([WorkStatus.APPROVED, WorkStatus.CLOSED]))
        )
    )


def _set_stale_since(reports: list[WorkReport], db: Session) -> None:
    now = datetime.now(timezone.utc)
    changed = False
    for report in reports:
        if report.stale_since is None:
            report.stale_since = now
            changed = True
    if changed:
        db.flush()


def _stale_elapsed_days(stale_since) -> int:
    return (_current_jst_date() - stale_since.date()).days


def _staff_users(db: Session) -> list[User]:
    users = db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
    return [user for user in users if has_role(user, "sales") or has_role(user, "office") or has_role(user, "admin_master")]


def _admin_master_users(db: Session) -> list[User]:
    return [
        user
        for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
        if has_role(user, "admin_master")
    ]


def _enqueue_stale_notification(db: Session, report: WorkReport, users: list[User], level: str) -> None:
    subject_by_level = {
        "remind": "【業務連絡表】未処理報告の確認",
        "warn": "【業務連絡表】未処理報告の警告",
        "escalate": "【業務連絡表】未処理報告の強制確認",
    }
    student_name = report.assignment.student_name if report.assignment else "生徒未設定"
    body = f"{student_name}の{report.target_month}月分報告が未処理です。ステータス: {report.status}"
    for user in users:
        record_notification(db, user, report, f"stale_report_{level}", subject_by_level[level], body)


def daily_stale_check(db: Session) -> None:
    stale = _stale_reports(db)
    if not stale:
        return

    _set_stale_since(stale, db)

    remind_days = int(os.getenv("STALE_REMIND_DAYS", "7"))
    warn_days = int(os.getenv("STALE_WARN_DAYS", "14"))
    escalate_days = int(os.getenv("STALE_ESCALATE_DAYS", "30"))
    staff = _staff_users(db)
    masters = _admin_master_users(db)

    for report in stale:
        if report.stale_since is None:
            continue
        elapsed = _stale_elapsed_days(report.stale_since)
        if elapsed >= escalate_days:
            _enqueue_stale_notification(db, report, masters, "escalate")
        elif elapsed >= warn_days:
            _enqueue_stale_notification(db, report, masters, "warn")
        elif elapsed >= remind_days:
            _enqueue_stale_notification(db, report, staff, "remind")
    db.commit()


def run_reminder_job() -> None:
    db = SessionLocal()
    try:
        enqueue_month_end_reminders(db)
        enqueue_school_approval_reminders(db)
        db.commit()
    finally:
        db.close()


def _run_stale_job() -> None:
    db = SessionLocal()
    try:
        daily_stale_check(db)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.TIMEZONE))
    scheduler.add_job(run_reminder_job, "cron", hour=9, minute=0, id="month_end_reminders", replace_existing=True)
    scheduler.add_job(_run_stale_job, "cron", hour=6, minute=0, id="stale_report_check", replace_existing=True)
    scheduler.start()
    return scheduler
