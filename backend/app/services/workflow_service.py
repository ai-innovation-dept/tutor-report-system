# === Phase 5: 承認ワークフロー START ===
from collections import defaultdict
from datetime import datetime, timezone
from logging import getLogger
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rbac import has_role
from app.models import ChatMessage, LessonReport, ReportAction, ReportEvent, ReportStatus, User
from app.services.notification_service import enqueue, send_email_notification

logger = getLogger(__name__)

APPROVAL_REQUEST_SUBJECT = "【指導実績】承認依頼が届きました"
RETURNED_SUBJECT = "【指導実績】差戻しコメントが届きました"
PARENT_APPROVED_SUBJECT = "【指導実績】保護者が承認しました"
SUBMITTED_TO_ADMIN_SUBJECT = "【指導実績】報告書が提出されました"
ADMIN_RETURN_SUBJECT = "【指導実績】運営から差戻しがありました"
ADMIN_APPROVED_SUBJECT = "【指導実績】最終承認が完了しました"


TRANSITIONS = {
    ReportAction.submit_to_parent.value: ("tutor", [ReportStatus.draft.value, ReportStatus.returned_to_tutor.value], ReportStatus.awaiting_parent_approval.value, "submitted_to_parent_at"),
    ReportAction.parent_approve.value: ("parent", [ReportStatus.awaiting_parent_approval.value], ReportStatus.parent_approved.value, "parent_approved_at"),
    ReportAction.parent_return.value: ("parent", [ReportStatus.awaiting_parent_approval.value], ReportStatus.returned_to_tutor.value, None),
    ReportAction.submit_to_admin.value: ("tutor", [ReportStatus.parent_approved.value], ReportStatus.submitted_to_admin.value, "submitted_to_admin_at"),
    ReportAction.receive.value: ("admin_receiver", [ReportStatus.submitted_to_admin.value, ReportStatus.returned_to_receiver.value], ReportStatus.received.value, "received_at"),
    ReportAction.return_from_receiver.value: ("admin_receiver", [ReportStatus.submitted_to_admin.value, ReportStatus.received.value, ReportStatus.returned_to_receiver.value], ReportStatus.returned_to_tutor.value, None),
    ReportAction.re_review.value: ("admin_reviewer", [ReportStatus.received.value], ReportStatus.re_reviewed.value, "re_reviewed_at"),
    ReportAction.return_from_reviewer.value: ("admin_reviewer", [ReportStatus.received.value, ReportStatus.re_reviewed.value], ReportStatus.returned_to_receiver.value, None),
    ReportAction.admin_approve.value: ("admin_master", [ReportStatus.re_reviewed.value], ReportStatus.admin_approved.value, "admin_approved_at"),
    ReportAction.return_from_master.value: ("admin_master", [ReportStatus.re_reviewed.value, ReportStatus.admin_approved.value], ReportStatus.returned_to_receiver.value, None),
}

RETURN_ACTIONS = {
    ReportAction.parent_return.value,
    ReportAction.return_from_receiver.value,
    ReportAction.return_from_reviewer.value,
    ReportAction.return_from_master.value,
}


def _role_allowed(required: str, actor: User) -> bool:
    return has_role(actor, required) or (has_role(actor, "admin_master") and required.startswith("admin_"))


def transition(db: Session, report: LessonReport, actor: User, action: str, comment: str | None = None) -> LessonReport:
    rule = TRANSITIONS.get(action)
    if not rule:
        raise HTTPException(status_code=400, detail="unknown action")
    required_role, allowed_from, to_status, timestamp_field = rule
    if not _role_allowed(required_role, actor):
        raise HTTPException(status_code=403, detail="action not allowed for role")
    if action in RETURN_ACTIONS and not comment:
        raise HTTPException(status_code=400, detail="return comment is required")
    if report.status not in allowed_from:
        raise HTTPException(status_code=409, detail=f"invalid transition from {report.status}")
    skip_parent_approval = action == ReportAction.submit_to_parent.value and bool(report.assignment and report.assignment.skip_parent_approval)
    if skip_parent_approval:
        to_status = ReportStatus.submitted_to_admin.value
        timestamp_field = "submitted_to_admin_at"

    old_status = report.status
    report.status = to_status
    if timestamp_field:
        setattr(report, timestamp_field, datetime.now(timezone.utc))
    db.add(ReportEvent(report_id=report.id, actor_id=actor.id, action=action, from_status=old_status, to_status=to_status, comment=comment))
    if action in RETURN_ACTIONS and comment:
        db.add(ChatMessage(report_id=report.id, sender_id=actor.id, body=f"差戻し理由: {comment}"))

    recipients = []
    if skip_parent_approval:
        recipients.append(report.tutor_id)
        recipients.extend(
            user.id
            for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
            if has_role(user, "admin_receiver")
        )
    elif to_status in {ReportStatus.awaiting_parent_approval.value}:
        recipients.append(report.parent_id)
    elif to_status in {ReportStatus.returned_to_tutor.value, ReportStatus.parent_approved.value}:
        recipients.append(report.tutor_id)
    elif to_status in {ReportStatus.submitted_to_admin.value, ReportStatus.received.value, ReportStatus.re_reviewed.value, ReportStatus.admin_approved.value}:
        recipients.extend([report.tutor_id, report.parent_id])
    for user_id in {recipient for recipient in recipients if recipient is not None}:
        enqueue(db, user_id, "status_changed", f"Report status changed: {to_status}", f"Report {report.id} moved from {old_status} to {to_status}.", report.id)
    return report


def auto_submit_to_admin(db: Session, reports: list[LessonReport], actor: User) -> None:
    now = datetime.now(timezone.utc)
    for report in reports:
        if report.status != ReportStatus.parent_approved.value:
            raise HTTPException(status_code=409, detail=f"invalid transition from {report.status}")
        old_status = report.status
        report.status = ReportStatus.submitted_to_admin.value
        report.submitted_to_admin_at = now
        db.add(
            ReportEvent(
                report_id=report.id,
                actor_id=actor.id,
                action=ReportAction.submit_to_admin.value,
                from_status=old_status,
                to_status=ReportStatus.submitted_to_admin.value,
            )
        )
        for user_id in {report.tutor_id, report.parent_id} - {None}:
            enqueue(
                db,
                user_id,
                "status_changed",
                f"Report status changed: {ReportStatus.submitted_to_admin.value}",
                f"Report {report.id} moved from {old_status} to {ReportStatus.submitted_to_admin.value}.",
                report.id,
            )


async def send_transition_notifications(db: Session, action: str, reports: list[LessonReport], actor: User, comment: str | None = None) -> None:
    grouped_reports = _group_reports(reports)
    for group_reports in grouped_reports.values():
        await _send_group_notification(db, action, group_reports, actor, comment)


def _group_reports(reports: list[LessonReport]) -> dict[tuple[str, str], list[LessonReport]]:
    grouped: dict[tuple[str, str], list[LessonReport]] = defaultdict(list)
    for report in reports:
        grouped[(str(report.assignment_id), report.target_month)].append(report)
    return grouped


def _base_url() -> str:
    return settings.base_url.rstrip("/")


def _format_lesson_date(report: LessonReport) -> str:
    return f"{report.lesson_date.year}/{report.lesson_date.month}/{report.lesson_date.day}"


def _duration_label(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    if hours and mins:
        return f"{hours}時間{mins}分"
    if hours:
        return f"{hours}時間"
    return f"{mins}分"


def _report_minutes(report: LessonReport) -> int:
    start = report.start_time.hour * 60 + report.start_time.minute
    end = report.end_time.hour * 60 + report.end_time.minute
    return max(0, end - start - (report.break_minutes or 0))


def _total_minutes(reports: list[LessonReport]) -> int:
    return sum(_report_minutes(report) for report in reports)


async def _send_email(db: Session, to_user: User | None, subject: str, template_name: str, context: dict) -> None:
    if not to_user:
        return
    try:
        await send_email_notification(to_user.email, subject, template_name, context)
    except Exception:
        logger.exception("failed to send workflow notification to %s", to_user.email)


async def _send_email_to_users(db: Session, users: list[User], subject: str, template_name: str, context: dict) -> None:
    for user in users:
        if user.is_active:
            await _send_email(db, user, subject, template_name, context | {"name": user.display_name})


def _assignment(report: LessonReport):
    return report.assignment


def _tutor(report: LessonReport) -> User | None:
    return report.tutor


def _parent(report: LessonReport) -> User | None:
    assignment_parent = _assignment(report).parent if _assignment(report) else None
    return report.parent or assignment_parent


def _student_name(report: LessonReport) -> str:
    assignment = _assignment(report)
    return assignment.student_name if assignment else "未設定"


async def _send_group_notification(db: Session, action: str, reports: list[LessonReport], actor: User, comment: str | None = None) -> None:
    if not reports:
        return
    report = sorted(reports, key=lambda item: (item.lesson_date, item.start_time))[0]
    count = len(reports)
    total_hours = _duration_label(_total_minutes(reports))
    context = {
        "base_url": _base_url(),
        "target_month": report.target_month,
        "student_name": _student_name(report),
        "count": count,
        "total_hours": total_hours,
    }

    if action == ReportAction.submit_to_parent.value:
        if report.assignment and report.assignment.skip_parent_approval:
            receivers = [
                user
                for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
                if has_role(user, "admin_receiver")
            ]
            await _send_email_to_users(
                db,
                receivers,
                SUBMITTED_TO_ADMIN_SUBJECT,
                "notify_submitted_to_admin.txt",
                context | {
                    "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
                },
            )
            return
        parent = _parent(report)
        await _send_email(
            db,
            parent,
            APPROVAL_REQUEST_SUBJECT,
            "notify_approval_request.txt",
            context | {
                "parent_name": parent.display_name if parent else "保護者",
                "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
            },
        )
        return

    if action == ReportAction.parent_return.value:
        tutor = _tutor(report)
        await _send_email(
            db,
            tutor,
            RETURNED_SUBJECT,
            "notify_returned.txt",
            context | {
                "tutor_name": tutor.display_name if tutor else "講師",
                "actor_name": actor.display_name,
                "lesson_date": _format_lesson_date(report),
                "comment": comment or "",
            },
        )
        return

    if action == ReportAction.parent_approve.value:
        tutor = _tutor(report)
        await _send_email(
            db,
            tutor,
            PARENT_APPROVED_SUBJECT,
            "notify_parent_approved.txt",
            context | {
                "tutor_name": tutor.display_name if tutor else "講師",
                "parent_name": actor.display_name,
            },
        )
        return

    if action == ReportAction.submit_to_admin.value:
        receivers = [
            user
            for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
            if has_role(user, "admin_receiver")
        ]
        await _send_email_to_users(
            db,
            receivers,
            SUBMITTED_TO_ADMIN_SUBJECT,
            "notify_submitted_to_admin.txt",
            context | {
                "tutor_name": _tutor(report).display_name if _tutor(report) else "講師",
            },
        )
        return

    if action == ReportAction.return_from_receiver.value:
        tutor = _tutor(report)
        await _send_email(
            db,
            tutor,
            ADMIN_RETURN_SUBJECT,
            "notify_returned.txt",
            context | {
                "tutor_name": tutor.display_name if tutor else "講師",
                "actor_name": actor.display_name,
                "lesson_date": _format_lesson_date(report),
                "comment": comment or "",
            },
        )
        return

    if action in {ReportAction.return_from_reviewer.value, ReportAction.return_from_master.value}:
        receivers = [
            user
            for user in db.scalars(select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))).all()
            if has_role(user, "admin_receiver")
        ]
        await _send_email_to_users(
            db,
            receivers,
            ADMIN_RETURN_SUBJECT,
            "notify_returned.txt",
            context | {
                "actor_name": actor.display_name,
                "lesson_date": _format_lesson_date(report),
                "comment": comment or "",
            },
        )
        return

    if action == ReportAction.admin_approve.value:
        tutor = _tutor(report)
        parent = _parent(report)
        if tutor:
            await _send_email(
                db,
                tutor,
                ADMIN_APPROVED_SUBJECT,
                "notify_admin_approved.txt",
                context | {
                    "name": tutor.display_name,
                },
            )
        if parent:
            await _send_email(
                db,
                parent,
                ADMIN_APPROVED_SUBJECT,
                "notify_admin_approved.txt",
                context | {
                    "name": parent.display_name,
                },
            )
# === Phase 5 END ===
