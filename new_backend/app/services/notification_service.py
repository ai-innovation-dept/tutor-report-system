"""通知サービス（メール送信・通知レコード作成）。"""
from collections import defaultdict
import logging
from pathlib import Path

from app.core.config import settings

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import Assignment, User
from app.models.work import WorkNotification, WorkReport
from app.workflow.definitions import WorkAction, WorkStatus

logger = logging.getLogger(__name__)


_APPROVAL_REQUEST_SUBJECT = "【業務連絡表】承認依頼が届きました"
_RETURNED_SUBJECT = "【業務連絡表】差戻しがありました"
_FINAL_APPROVED_SUBJECT = "【業務連絡表】最終承認が完了しました"
_APPROVED_BY_SCHOOL_SUBJECT = "【業務連絡表】学校が承認しました"
_SUBMITTED_TO_ADMIN_SUBJECT = "【業務連絡表】報告書が提出されました"

_NOTIFICATION_RULES: dict[tuple[str, str], tuple[tuple[str, ...], str, str]] = {
    ("submit", "draft"): (("school",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("submit", "returned_to_tutor"): (("school",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("submit", "returned_to_office"): (("sales",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("skip_school", "draft"): (("office",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("approve", "awaiting_school"): (("tutor",), "approved_by_school", _APPROVED_BY_SCHOOL_SUBJECT),
    ("approve", "awaiting_office"): (("sales",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("approve", "returned_to_office"): (("sales",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("approve", "awaiting_sales"): (("admin_master",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("approve", "awaiting_finance"): (("tutor", "school"), "final_approved", _FINAL_APPROVED_SUBJECT),
    ("return", "awaiting_school"): (("tutor",), "returned", _RETURNED_SUBJECT),
    ("return", "awaiting_office"): (("tutor",), "returned", _RETURNED_SUBJECT),
    ("return", "awaiting_sales"): (("office",), "returned", _RETURNED_SUBJECT),
    ("return", "awaiting_finance"): (("office",), "returned", _RETURNED_SUBJECT),
    ("return", "approved"): (("office",), "returned", _RETURNED_SUBJECT),
    ("return", "returned_to_office"): (("tutor",), "returned", _RETURNED_SUBJECT),
}


def record_notification(
    db: Session,
    user: User,
    report: WorkReport,
    notif_type: str,
    subject: str,
    body: str,
) -> WorkNotification:
    notif = WorkNotification(
        user_id=user.id,
        report_id=report.id,
        channel="email",
        type=notif_type,
        subject=subject,
        body=body,
        sent_at=None,
    )
    db.add(notif)
    return notif


def _resolve_notification_recipients(db: Session, report: WorkReport, recipient_role: str) -> list[User]:
    if recipient_role == "tutor":
        user = db.get(User, report.tutor_id)
        return [user] if user and user.is_active else []

    if recipient_role == "school":
        assignment = report.assignment or db.get(Assignment, report.assignment_id)
        if assignment is None or assignment.parent_id is None:
            return []
        user = db.get(User, assignment.parent_id)
        return [user] if user and user.is_active else []

    if recipient_role in {"sales", "office", "admin_master"}:
        return list(
            db.scalars(
                select(User).where(
                    User.role == recipient_role,
                    User.is_active.is_(True),
                )
            )
        )

    return []


def _notification_body(report: WorkReport, action: str, from_status: str) -> str:
    return (
        "業務連絡表のワークフロー状態が更新されました。\n"
        f"報告書ID: {report.id}\n"
        f"対象月: {report.target_month}\n"
        f"操作: {action}\n"
        f"遷移前ステータス: {from_status}\n"
        f"現在ステータス: {report.status}"
    )


def _enqueue_notification(
    db: Session,
    report: WorkReport,
    action: str,
    from_status: str,
    actor: User,
) -> list[WorkNotification]:
    """ワークフロー遷移に対応する通知レコードを作成する。メール実送信は行わない。"""
    del actor  # 現時点では監査イベント側に保持し、通知文面には使わない。

    if action == "close":
        return []

    rule = _NOTIFICATION_RULES.get((action, from_status))
    if rule is None:
        return []

    recipient_roles, notif_type, subject = rule
    # 学校スキップで提出が事務確認へ直行した場合は通知先も事務に切り替える
    if action == WorkAction.SUBMIT and report.status == WorkStatus.AWAITING_OFFICE:
        recipient_roles = ("office",)
    body = _notification_body(report, action, from_status)
    notifications: list[WorkNotification] = []

    for recipient_role in recipient_roles:
        recipients = _resolve_notification_recipients(db, report, recipient_role)
        if not recipients:
            logger.warning(
                "workflow notification skipped: recipient_role=%s report_id=%s action=%s from_status=%s",
                recipient_role,
                report.id,
                action,
                from_status,
            )
            continue
        for recipient in recipients:
            notifications.append(record_notification(db, recipient, report, notif_type, subject, body))

    return notifications


async def send_email(
    to: str,
    subject: str,
    body: str,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
) -> None:
    """DBへの記録なしで単体メールを送信する（招待・パスワードリセット用）。"""
    host = smtp_host or settings.SMTP_HOST
    port = smtp_port or settings.SMTP_PORT
    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = "noreply@work-system.local"
        msg["To"] = to
        await aiosmtplib.send(msg, hostname=host, port=port)
    except Exception as exc:
        logger.warning("mail send failed to %s: %s", to, exc)


def _render_email_template(template_name: str, context: dict) -> str:
    template_path = Path(__file__).resolve().parents[1] / "templates" / "email" / template_name
    template = template_path.read_text(encoding="utf-8")
    return template.format(**context)


async def send_email_notification(to_email: str, subject: str, template_name: str, context: dict) -> None:
    body = _render_email_template(template_name, context)
    await send_email(to_email, subject, body)


def _group_reports(reports: list[WorkReport]) -> dict[tuple[str, str], list[WorkReport]]:
    grouped: dict[tuple[str, str], list[WorkReport]] = defaultdict(list)
    for report in reports:
        grouped[(str(report.assignment_id), report.target_month)].append(report)
    return grouped


def _base_url() -> str:
    return settings.BASE_URL.rstrip("/")


def _duration_label(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    if hours and mins:
        return f"{hours}時間{mins}分"
    if hours:
        return f"{hours}時間"
    return f"{mins}分"


def _report_minutes(report: WorkReport) -> int:
    total = 0
    for line in (report.form_data or {}).get("lines", []):
        try:
            total += int(line.get("teach_minutes", 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _total_minutes(reports: list[WorkReport]) -> int:
    return sum(_report_minutes(report) for report in reports)


def _assignment(report: WorkReport) -> Assignment | None:
    return report.assignment


def _tutor(report: WorkReport) -> User | None:
    return report.tutor


def _school(db: Session, report: WorkReport) -> User | None:
    assignment = _assignment(report) or db.get(Assignment, report.assignment_id)
    if not assignment or not assignment.parent_id:
        return None
    return assignment.parent


def _student_name(report: WorkReport) -> str:
    assignment = _assignment(report)
    return assignment.student_name if assignment else "未設定"


def _staff_users(db: Session, role: str) -> list[User]:
    return list(
        db.scalars(
            select(User).where(
                User.role == role,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
    )


async def _send_email(db: Session, to_user: User | None, subject: str, template_name: str, context: dict) -> None:
    del db
    if not to_user or not to_user.is_active:
        return
    try:
        await send_email_notification(to_user.email, subject, template_name, context)
    except Exception as exc:
        logger.warning("failed to send workflow notification to %s: %s", to_user.email, exc)


async def _send_email_to_users(db: Session, users: list[User], subject: str, template_name: str, context: dict) -> None:
    for user in users:
        if user.is_active:
            await _send_email(db, user, subject, template_name, context | {"name": user.display_name})


def _lesson_date_label(report: WorkReport) -> str:
    return report.target_month


async def send_transition_notifications(
    db: Session,
    action: str,
    reports: list[WorkReport],
    actor: User,
    comment: str | None = None,
) -> None:
    grouped_reports = _group_reports(reports)
    for group_reports in grouped_reports.values():
        await _send_group_notification(db, action, group_reports, actor, comment)


async def _send_group_notification(
    db: Session,
    action: str,
    reports: list[WorkReport],
    actor: User,
    comment: str | None = None,
) -> None:
    if not reports:
        return
    report = sorted(reports, key=lambda item: item.created_at)[0]
    count = len(reports)
    total_hours = _duration_label(_total_minutes(reports))
    context = {
        "base_url": _base_url(),
        "target_month": report.target_month,
        "student_name": _student_name(report),
        "count": count,
        "total_hours": total_hours,
    }

    if action in {WorkAction.SUBMIT, WorkAction.SKIP_SCHOOL}:
        if action == WorkAction.SUBMIT and report.status == WorkStatus.AWAITING_SCHOOL:
            school = _school(db, report)
            await _send_email(
                db,
                school,
                _APPROVAL_REQUEST_SUBJECT,
                "notify_approval_request.txt",
                context | {
                    "parent_name": school.display_name if school else "学校",
                    "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
                },
            )
            return
        receiver_role = "sales" if report.status == WorkStatus.AWAITING_SALES else "office"
        receivers = _staff_users(db, receiver_role)
        await _send_email_to_users(
            db,
            receivers,
            _SUBMITTED_TO_ADMIN_SUBJECT,
            "notify_submitted_to_admin.txt",
            context | {
                "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
            },
        )
        return

    if action == WorkAction.RETURN:
        if report.status == WorkStatus.RETURNED_TO_TUTOR:
            tutor = _tutor(report)
            await _send_email(
                db,
                tutor,
                _RETURNED_SUBJECT,
                "notify_returned.txt",
                context | {
                    "tutor_name": tutor.display_name if tutor else "講師",
                    "actor_name": actor.display_name,
                    "lesson_date": _lesson_date_label(report),
                    "comment": comment or "",
                },
            )
            return
        if report.status == WorkStatus.RETURNED_TO_OFFICE:
            receivers = _staff_users(db, "office")
            await _send_email_to_users(
                db,
                receivers,
                _RETURNED_SUBJECT,
                "notify_returned.txt",
                context | {
                    "actor_name": actor.display_name,
                    "lesson_date": _lesson_date_label(report),
                    "comment": comment or "",
                },
            )
            return

    if action == WorkAction.APPROVE:
        if report.status == WorkStatus.AWAITING_OFFICE:
            tutor = _tutor(report)
            await _send_email(
                db,
                tutor,
                _APPROVED_BY_SCHOOL_SUBJECT,
                "notify_parent_approved.txt",
                context | {
                    "tutor_name": tutor.display_name if tutor else "講師",
                    "parent_name": actor.display_name,
                },
            )
            return
        if report.status == WorkStatus.AWAITING_SALES:
            await _send_email_to_users(
                db,
                _staff_users(db, "sales"),
                _APPROVAL_REQUEST_SUBJECT,
                "notify_submitted_to_admin.txt",
                context | {
                    "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
                },
            )
            return
        if report.status == WorkStatus.AWAITING_FINANCE:
            await _send_email_to_users(
                db,
                _staff_users(db, "admin_master"),
                _APPROVAL_REQUEST_SUBJECT,
                "notify_submitted_to_admin.txt",
                context | {
                    "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
                },
            )
            return
        if report.status == WorkStatus.APPROVED:
            tutor = _tutor(report)
            school = _school(db, report)
            if tutor:
                await _send_email(
                    db,
                    tutor,
                    _FINAL_APPROVED_SUBJECT,
                    "notify_admin_approved.txt",
                    context | {"name": tutor.display_name},
                )
            if school:
                await _send_email(
                    db,
                    school,
                    _FINAL_APPROVED_SUBJECT,
                    "notify_admin_approved.txt",
                    context | {"name": school.display_name},
                )


async def send_notification(
    db: Session,
    user: User,
    report: WorkReport,
    notif_type: str,
    subject: str,
    body: str,
    smtp_host: str = "mailhog",
    smtp_port: int = 1025,
) -> None:
    del smtp_host, smtp_port
    record_notification(db, user, report, notif_type, subject, body)
