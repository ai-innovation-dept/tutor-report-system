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
REPORT_MODIFIED_SUBJECT = "【指導実績】報告書が修正されました"


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

# 職務分掌：受付工程(receive)と再鑑工程(re_review)は、同一「報告書」に対して同一スタッフが
# 兼務できない。ある報告書でどちらかの工程を判断（承認・差戻しのいずれも）すると、
# その「同じ報告書」のもう一方の工程は不可になる。
# スコープは報告書単位であり、同一講師でも別の報告書（別生徒・別月）には影響しない
# （例：報告書Xを受付承認した人は報告書Xの再鑑承認・再鑑差戻しはできないが、別報告書Yは可）。
# admin_master / admin_chief（最終承認者・フルアクセス）はこの制約の対象外。
# キー=これから行う操作、値=その操作を不可にする「同一報告書での担当済み」承認アクション。
SEPARATION_CONFLICT = {
    ReportAction.receive.value: ReportAction.re_review.value,
    ReportAction.re_review.value: ReportAction.receive.value,
    # 差戻しも工程上の判断のため、承認と同様に兼務不可とする
    ReportAction.return_from_receiver.value: ReportAction.re_review.value,
    ReportAction.return_from_reviewer.value: ReportAction.receive.value,
}
_SEPARATION_MESSAGE = {
    ReportAction.receive.value: "この報告書はあなたが再鑑承認を担当済みのため、受付承認はできません（受付と再鑑は同一報告書で同一人物が兼務できません）。",
    ReportAction.re_review.value: "この報告書はあなたが受付承認を担当済みのため、再鑑承認はできません（受付と再鑑は同一報告書で同一人物が兼務できません）。",
    ReportAction.return_from_receiver.value: "この報告書はあなたが再鑑承認を担当済みのため、受付差戻しはできません（受付と再鑑は同一報告書で同一人物が兼務できません）。",
    ReportAction.return_from_reviewer.value: "この報告書はあなたが受付承認を担当済みのため、再鑑差戻しはできません（受付と再鑑は同一報告書で同一人物が兼務できません）。",
}


def _role_allowed(required: str, actor: User) -> bool:
    return has_role(actor, required) or (
        (has_role(actor, "admin_master") or has_role(actor, "admin_chief")) and required.startswith("admin_")
    )


def _reports_acted_by(db: Session, actor_id, action: str) -> set:
    """actor が指定アクション(receive/re_review)を実施した報告書IDの集合を返す。"""
    rows = db.scalars(
        select(ReportEvent.report_id)
        .where(ReportEvent.actor_id == actor_id, ReportEvent.action == action)
        .distinct()
    ).all()
    return set(rows)


def _assert_separation_of_duties(db: Session, report: LessonReport, actor: User, action: str) -> None:
    conflicting = SEPARATION_CONFLICT.get(action)
    if not conflicting or has_role(actor, "admin_master") or has_role(actor, "admin_chief"):
        return
    if report.id in _reports_acted_by(db, actor.id, conflicting):
        raise HTTPException(status_code=409, detail=_SEPARATION_MESSAGE[action])


def separation_locks(db: Session, actor: User) -> dict[str, list[str]]:
    """UI用：現在のユーザーが受付/再鑑を担当済みの報告書ID一覧。admin_master / admin_chief は対象外のため空。"""
    if has_role(actor, "admin_master") or has_role(actor, "admin_chief"):
        return {"received_report_ids": [], "reviewed_report_ids": []}
    return {
        "received_report_ids": [str(rid) for rid in _reports_acted_by(db, actor.id, ReportAction.receive.value)],
        "reviewed_report_ids": [str(rid) for rid in _reports_acted_by(db, actor.id, ReportAction.re_review.value)],
    }


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
    _assert_separation_of_duties(db, report, actor, action)
    skip_parent_approval = action == ReportAction.submit_to_parent.value and bool(report.parent and report.parent.skip_parent_approval)
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


def _format_changes(changes: list[tuple[str, str, str]]) -> str:
    """(項目名, 変更前, 変更後) のリストを本文用の差分テキストに整形する。

    テンプレートは str.format で展開されるため、本文に紛れ込む波括弧は二重化してエスケープする。
    """
    def esc(value: str) -> str:
        return str(value).replace("{", "{{").replace("}", "}}")

    lines: list[str] = []
    for label, old, new in changes:
        if "\n" in str(old) or "\n" in str(new) or len(str(old)) > 30 or len(str(new)) > 30:
            lines.append(f"・{label}：\n　（修正前）{esc(old)}\n　（修正後）{esc(new)}")
        else:
            lines.append(f"・{label}：{esc(old)} → {esc(new)}")
    return "\n".join(lines)


async def notify_report_modified(
    db: Session, report: LessonReport, changes: list[tuple[str, str, str]], actor: User
) -> None:
    """受付による報告書修正を講師・保護者へ通知する。保護者は未設定/承認スキップなら送らない。"""
    context = {
        "base_url": _base_url(),
        "student_name": _student_name(report),
        "target_month": report.target_month,
        "lesson_date": _format_lesson_date(report),
        "actor_name": actor.display_name,
        "changes": _format_changes(changes),
    }
    tutor = _tutor(report)
    await _send_email(
        db, tutor, REPORT_MODIFIED_SUBJECT, "notify_report_modified.txt",
        context | {"name": tutor.display_name if tutor else "講師"},
    )
    parent = _parent(report)
    if parent and not parent.skip_parent_approval:
        await _send_email(
            db, parent, REPORT_MODIFIED_SUBJECT, "notify_report_modified.txt",
            context | {"name": parent.display_name},
        )


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
        if report.parent and report.parent.skip_parent_approval:
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
