# === Phase 7: 通知・リマインダー START ===
import calendar
import os
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.rbac import has_role
from app.core.time import get_current_jst_date
from app.database import SessionLocal
from app.models import LessonReport, ReportStatus, User
from app.services.notification_service import enqueue
from app.services.report_service import get_stale_reports, set_stale_since


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


def _stale_elapsed_days(stale_since) -> int:
    return (get_current_jst_date() - stale_since.date()).days


def _staff_users(db: Session) -> list[User]:
    users = db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
    return [
        user
        for user in users
        if has_role(user, "admin_receiver") or has_role(user, "admin_reviewer") or has_role(user, "admin_master")
    ]


def _admin_master_users(db: Session) -> list[User]:
    return [
        user
        for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
        if has_role(user, "admin_master")
    ]


def _enqueue_stale_notification(db: Session, report: LessonReport, users: list[User], level: str) -> None:
    subject_by_level = {
        "remind": "【指導実績】未処理報告の確認",
        "warn": "【指導実績】未処理報告の警告",
        "escalate": "【指導実績】未処理報告の強制確認",
    }
    student_name = report.assignment.student_name if report.assignment else "生徒未設定"
    body = f"{student_name}の{report.target_month}月分報告が未処理です。ステータス: {report.status}"
    for user in users:
        enqueue(db, user.id, f"stale_report_{level}", subject_by_level[level], body, report.id)


def daily_stale_check(db: Session) -> None:
    """
    毎朝 AM6:00(JST) 実行。
    stale_since のセットと経過日数に応じた通知のみを行う。自動クローズは行わない。
    """
    stale = get_stale_reports(db)
    if not stale:
        return

    set_stale_since(stale, db)

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


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(run_reminder_job, "cron", hour=9, minute=0, id="month_end_reminders", replace_existing=True)
    scheduler.add_job(_run_stale_job, "cron", hour=6, minute=0, id="stale_report_check", replace_existing=True)
    scheduler.start()
    return scheduler


def _run_stale_job() -> None:
    db = SessionLocal()
    try:
        daily_stale_check(db)
    finally:
        db.close()
# === Phase 7 END ===
