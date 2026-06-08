# === Phase 7: 通知・リマインダー START ===
import asyncio
import calendar
import os
from datetime import date, datetime, timedelta, timezone
from logging import getLogger
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.rbac import has_role
from app.core.time import get_current_jst_date
from app.database import SessionLocal
from app.models import LessonReport, Notification, ReportStatus, User
from app.services.notification_service import EmailChannel, enqueue
from app.services.report_service import get_stale_reports, set_stale_since

logger = getLogger(__name__)

APPROVAL_REMINDER_TYPE = "reminder_approval"


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


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _approval_reminder_body(assignment) -> str:
    base_url = settings.base_url.rstrip("/")
    return (
        f"{assignment.student_name}の指導実績報告書の承認をお願いします。\n\n"
        f"以下より内容のご確認・承認をお願いいたします。\n{base_url}/parent/approval"
    )


def enqueue_approval_reminders(db: Session, now: datetime | None = None) -> int:
    """保護者の承認依頼後、設定した間隔ごとにリマインド通知を作成する。

    ★テスト用「分」単位モード：Assignment.reminder_days_after を「分」として解釈する。
      （日単位運用へ戻す際は本関数とスケジューラ間隔・管理画面ラベルを日へ戻すこと）

    保護者が承認するまでエンドレスに作成する（回数上限なし）。承認されると報告書が
    awaiting_parent_approval から外れるため自然に停止する。
    判定は「承認依頼からの経過で本来送られているべき回数 > 既に作成済みの件数」の場合に
    1件作成する方式とし、ジョブの実行タイミングに依存しない・重複しないようにしている。
    """
    now = _as_utc(now or datetime.now(timezone.utc))
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
        interval_minutes = max(1, assignment.reminder_days_after)
        elapsed_minutes = (now - _as_utc(report.submitted_to_parent_at)).total_seconds() / 60
        due_count = int(elapsed_minutes // interval_minutes)
        if due_count <= 0:
            continue
        already_sent = db.scalar(
            select(func.count(Notification.id)).where(
                Notification.report_id == report.id,
                Notification.type == APPROVAL_REMINDER_TYPE,
            )
        ) or 0
        if due_count > already_sent:
            enqueue(
                db,
                report.parent_id,
                APPROVAL_REMINDER_TYPE,
                "【指導実績】承認のお願い（リマインド）",
                _approval_reminder_body(assignment),
                report.id,
            )
            count += 1
    if count:
        db.flush()
    return count


def _send_pending_approval_emails(db: Session) -> int:
    """未送信の承認リマインド通知を実際にメール送信し、sent_at を記録する。"""
    pending = db.scalars(
        select(Notification).where(
            Notification.type == APPROVAL_REMINDER_TYPE,
            Notification.sent_at.is_(None),
            Notification.channel == "email",
        )
    ).all()
    sent = 0
    for notification in pending:
        user = db.get(User, notification.user_id)
        if not user or not user.email:
            continue
        try:
            asyncio.run(EmailChannel().send(user.email, notification.subject, notification.body))
            notification.sent_at = datetime.now(timezone.utc)
            sent += 1
        except Exception:
            logger.exception("failed to send approval reminder email to %s", user.email)
    return sent


def run_approval_reminder_job() -> None:
    """承認リマインドの作成＋メール送信。スケジューラから毎分実行する（分単位テスト）。"""
    db = SessionLocal()
    try:
        enqueue_approval_reminders(db)
        db.flush()
        _send_pending_approval_emails(db)
        db.commit()
    finally:
        db.close()


def run_reminder_job() -> None:
    # 月末リマインドは日次。承認リマインドは run_approval_reminder_job（毎分）で処理する。
    db = SessionLocal()
    try:
        enqueue_month_end_reminders(db)
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
    # ★承認リマインドは分単位テストのため毎分実行（日単位運用へ戻す際は間隔も日次へ戻すこと）
    scheduler.add_job(run_approval_reminder_job, "interval", minutes=1, id="approval_reminders", replace_existing=True)
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
