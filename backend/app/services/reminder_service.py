# === Phase 7: 通知・リマインダー START ===
import calendar
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.time import get_current_jst_date
from app.database import SessionLocal
from app.models import LessonReport, ReportStatus
from app.services.notification_service import enqueue


def is_reminder_day(today: date, days_before_month_end: int) -> bool:
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.day >= last_day - days_before_month_end


def enqueue_month_end_reminders(db: Session, today: date | None = None) -> int:
    today = today or get_current_jst_date()
    if not is_reminder_day(today, settings.reminder_days_before_month_end):
        return 0
    count = 0
    rows = db.scalars(select(LessonReport).where(LessonReport.target_month == today.strftime("%Y-%m"))).all()
    for report in rows:
        if report.status == ReportStatus.awaiting_parent_approval.value:
            if report.parent_id:
                enqueue(db, report.parent_id, "reminder_unapproved", "Report approval reminder", "Please approve or return the pending report.", report.id)
                count += 1
        elif report.status in {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value, ReportStatus.parent_approved.value}:
            enqueue(db, report.tutor_id, "reminder_unsubmitted", "Report submission reminder", "Please complete and submit the report.", report.id)
            count += 1
    if count:
        db.flush()
    return count


def enqueue_approval_reminders(db: Session, today: date | None = None) -> int:
    """承認依頼からX日後にリマインドを送る"""
    today = today or get_current_jst_date()
    reports = db.scalars(
        select(LessonReport)
        .options(selectinload(LessonReport.assignment))
        .where(LessonReport.status == ReportStatus.awaiting_parent_approval.value)
        .where(LessonReport.submitted_to_parent_at.is_not(None))
    ).all()
    count = 0
    for report in reports:
        assignment = report.assignment
        if not assignment or not assignment.reminder_enabled:
            continue
        if not report.parent_id:
            continue
        submitted_date = report.submitted_to_parent_at.date()
        days_since = (today - submitted_date).days
        if days_since > 0 and days_since % assignment.reminder_days_after == 0:
            times_reminded = days_since // assignment.reminder_days_after
            if times_reminded <= assignment.reminder_count:
                enqueue(
                    db,
                    report.parent_id,
                    "reminder_approval",
                    "【指導実績】承認のお願い（リマインド）",
                    f"{assignment.student_name}の指導実績報告書の承認をお願いします。",
                    report.id,
                )
                count += 1
    if count:
        db.flush()
    return count


def run_reminder_job() -> None:
    db = SessionLocal()
    try:
        enqueue_month_end_reminders(db)
        enqueue_approval_reminders(db)
        db.commit()
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(run_reminder_job, "cron", hour=9, minute=0, id="month_end_reminders", replace_existing=True)
    scheduler.start()
    return scheduler
# === Phase 7 END ===
