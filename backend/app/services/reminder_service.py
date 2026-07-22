# === Phase 7: 通知・リマインダー START ===
import asyncio
import calendar
import os
from datetime import date, datetime, timezone
from logging import getLogger
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.rbac import has_role
from app.core.time import get_current_jst_date
from app.database import SessionLocal
from app.models import Assignment, LessonReport, Notification, ReportStatus, User
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


def _approval_reminder_body(assignment) -> str:
    base_url = settings.base_url.rstrip("/")
    tutor_name = assignment.tutor.display_name if assignment.tutor else "担当講師"
    return (
        "いつもお世話になっております。\n\n"
        f"担当講師の{tutor_name}より、{assignment.student_name}さんの指導実績報告書について"
        "承認のお願いをお送りしております。\n"
        "ご多用のところ恐れ入りますが、まだご確認いただけていないようでしたら、"
        "下記より内容をご確認のうえ、ご承認をお願いいたします。\n\n"
        f"▼ご確認・ご承認はこちら\n{base_url}/parent/approval\n\n"
        "※すでにご対応・ご承認済みの場合は、行き違いとなりますので何卒ご容赦ください。"
    )


def enqueue_approval_reminders(db: Session, today: date | None = None) -> int:
    """保護者の承認依頼後、設定した間隔日数ごとにリマインド通知を作成する。

    保護者が承認するまでエンドレスに作成する（回数上限なし）。承認されると報告書が
    awaiting_parent_approval から外れるため自然に停止する。
    判定は「承認依頼からの経過日数で本来送られているべき回数 > 既に作成済みの件数」の
    場合に1件作成する方式とし、ジョブの実行タイミングに依存しない・重複しないようにしている。
    有効/無効・間隔日数は案件(Assignment)単位で運営が設定する。
    """
    today = today or get_current_jst_date()
    reports = db.scalars(
        select(LessonReport)
        .options(selectinload(LessonReport.assignment).selectinload(Assignment.tutor))
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
        # 無効化・削除済みの保護者にはリマインドを作らない（無効化＝削除と同等の扱い）
        parent = db.get(User, report.parent_id)
        if not parent or not parent.is_active or parent.deleted_at:
            continue
        interval_days = max(1, assignment.reminder_days_after)
        elapsed_days = (today - report.submitted_to_parent_at.date()).days
        due_count = elapsed_days // interval_days
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
        # 無効化・削除済みユーザーには送らない（無効化＝削除と同等に宛先から外す）
        if not user.is_active or user.deleted_at:
            continue
        try:
            asyncio.run(EmailChannel().send(user.email, notification.subject, notification.body))
            notification.sent_at = datetime.now(timezone.utc)
            sent += 1
        except Exception:
            logger.exception("failed to send approval reminder email to %s", user.email)
    return sent




def run_reminder_job() -> None:
    # 日次（09:00 JST）。月末リマインド通知と、承認リマインド（作成＋実メール送信）を処理する。
    db = SessionLocal()
    try:
        enqueue_month_end_reminders(db)
        enqueue_approval_reminders(db)
        db.flush()
        _send_pending_approval_emails(db)
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
        if has_role(user, "admin_receiver") or has_role(user, "admin_reviewer") or has_role(user, "admin_master") or has_role(user, "admin_chief")
    ]


def _admin_master_users(db: Session) -> list[User]:
    return [
        user
        for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
        if has_role(user, "admin_master") or has_role(user, "admin_chief")
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
    from app.services.deadline_service import run_deadline_notice_job

    scheduler = BackgroundScheduler(timezone=ZoneInfo(settings.timezone))
    scheduler.add_job(run_reminder_job, "cron", hour=9, minute=0, id="month_end_reminders", replace_existing=True)
    # 提出締切通知メール（改修依頼 202607161428）。DEADLINE_NOTICE_ENABLED=true のときのみ実送信対象を投函する
    scheduler.add_job(run_deadline_notice_job, "cron", hour=9, minute=0, id="deadline_notices", replace_existing=True)
    scheduler.add_job(_run_stale_job, "cron", hour=6, minute=0, id="stale_report_check", replace_existing=True)
    # メール送信キューのドレイナ（実送信する smtp 時のみ起動）。送信間隔ごとに起動し、
    # 1通ずつ間隔をあけて順次送信する。max_instances=1+coalesce で多重実行を防ぐ。
    if (settings.mail_backend or "console").lower() == "smtp":
        from app.services.mailer import drain_outbox

        scheduler.add_job(
            drain_outbox,
            "interval",
            seconds=max(1, int(settings.mail_send_interval_seconds)),
            id="mail_drainer",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    return scheduler


def _run_stale_job() -> None:
    db = SessionLocal()
    try:
        daily_stale_check(db)
    finally:
        db.close()
# === Phase 7 END ===
