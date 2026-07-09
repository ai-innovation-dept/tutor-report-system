"""通知サービス（メール送信・通知レコード作成）。"""
from collections import defaultdict
import logging
from pathlib import Path

from app.core.config import settings

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import Assignment, User
from app.models.work import WorkNotification, WorkReport, WorkReportEvent
from app.services.mailer import enqueue_mail
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
    # 超過フロー: 事務の事前確認の承認で学校へ承認依頼、差戻しは講師へ
    ("approve", "awaiting_office_precheck"): (("school",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("return", "awaiting_office_precheck"): (("tutor",), "returned", _RETURNED_SUBJECT),
    ("approve", "awaiting_school"): (("tutor",), "approved_by_school", _APPROVED_BY_SCHOOL_SUBJECT),
    ("approve", "awaiting_office"): (("sales",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    ("approve", "returned_to_office"): (("sales",), "approval_request", _APPROVAL_REQUEST_SUBJECT),
    # 営業承認で最終承認（完了）。講師・学校へ完了通知
    ("approve", "awaiting_sales"): (("tutor", "school"), "final_approved", _FINAL_APPROVED_SUBJECT),
    ("return", "awaiting_school"): (("tutor",), "returned", _RETURNED_SUBJECT),
    ("return", "awaiting_office"): (("tutor",), "returned", _RETURNED_SUBJECT),
    ("return", "awaiting_sales"): (("office",), "returned", _RETURNED_SUBJECT),
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
        return [user] if user and user.is_active and not user.deleted_at else []

    if recipient_role == "school":
        assignment = report.assignment or db.get(Assignment, report.assignment_id)
        if assignment is None or assignment.parent_id is None:
            return []
        user = db.get(User, assignment.parent_id)
        return [user] if user and user.is_active and not user.deleted_at else []

    if recipient_role in {"sales", "office", "admin_master", "admin_chief"}:
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
    # 学校スキップで事務確認へ直行、または月分超過で事務の事前確認へ向かった場合は通知先を事務に切り替える
    if action == WorkAction.SUBMIT and report.status in (
        WorkStatus.AWAITING_OFFICE,
        WorkStatus.AWAITING_OFFICE_PRECHECK,
    ):
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


def _render_email_template(template_name: str, context: dict) -> str:
    template_path = Path(__file__).resolve().parents[1] / "templates" / "email" / template_name
    template = template_path.read_text(encoding="utf-8")
    return template.format(**context)


def enqueue_email_template(db: Session, to_email: str, subject: str, template_name: str, context: dict) -> None:
    """テンプレートを描画し、メール送信キュー（アウトボックス）へ投函する。

    即時送信はしない。実送信はバックグラウンドのドレイナ(mailer.drain_outbox)が
    1通ずつ間隔をあけて行う。
    """
    body = _render_email_template(template_name, context)
    enqueue_mail(db, to_email, subject, body)


def _group_reports(reports: list[WorkReport]) -> dict[tuple[str, str], list[WorkReport]]:
    grouped: dict[tuple[str, str], list[WorkReport]] = defaultdict(list)
    for report in reports:
        grouped[(str(report.assignment_id), report.target_month)].append(report)
    return grouped


def _base_url() -> str:
    return settings.NEW_BASE_URL.rstrip("/")


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
    if not to_user or not to_user.is_active or to_user.deleted_at:
        return
    try:
        # 即時送信せず送信キューへ投函する（実送信はドレイナが順次・間隔をあけて行う）
        enqueue_email_template(db, to_user.email, subject, template_name, context)
    except Exception as exc:  # noqa: BLE001 - 通知の失敗は主処理（承認等）を止めない
        logger.warning("failed to enqueue workflow notification to %s: %s", to_user.email, exc)


async def _send_email_to_users(db: Session, users: list[User], subject: str, template_name: str, context: dict) -> None:
    for user in users:
        if user.is_active and not user.deleted_at:
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

    if action == WorkAction.APPROVE:
        # 学校承認（awaiting_school → awaiting_office）で契約講師全員の承認が揃った学校を
        # 営業へ通知する（循環importを避けるため遅延import）。
        from app.services.school_progress_service import send_school_all_approved_notifications

        await send_school_all_approved_notifications(db, reports)


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


_OFFICE_EDITED_SUBJECT = "【業務連絡表】報告書が修正されました"


def _format_office_changes(changes: list[tuple[str, str, str]] | None) -> str:
    """(項目名, 修正前, 修正後) のリストをメール本文用の差分テキストに整形する。"""
    if not changes:
        return "（明細の変更はありません）"
    lines: list[str] = []
    for label, old, new in changes:
        if "\n" in str(old) or "\n" in str(new) or len(str(old)) > 30 or len(str(new)) > 30:
            lines.append(f"・{label}：\n　（修正前）{old}\n　（修正後）{new}")
        else:
            lines.append(f"・{label}：{old} → {new}")
    return "\n".join(lines)


async def send_office_edit_notification(
    db: Session,
    report: WorkReport,
    actor: User,
    comment: str | None = None,
    changes: list[tuple[str, str, str]] | None = None,
) -> None:
    """事務担当が報告書を修正した際に、修正前との差分を講師・学校へ通知する。

    学校（assignment.parent）は未設定または学校確認スキップ設定なら送らない
    （既存システムの保護者通知条件と同じ）。
    """
    comment_text = (comment or "").strip()
    comment_block = f"{actor.display_name}からの連絡：{comment_text}\n\n" if comment_text else ""
    context = {
        "base_url": _base_url(),
        "target_month": report.target_month,
        "student_name": _student_name(report),
        "actor_name": actor.display_name,
        "changes": _format_office_changes(changes),
        "comment_block": comment_block,
    }
    tutor = _tutor(report)
    if tutor:
        await _send_email(
            db,
            tutor,
            _OFFICE_EDITED_SUBJECT,
            "notify_office_edited.txt",
            context | {"name": tutor.display_name},
        )
    school = _school(db, report)
    if school and not school.skip_parent_approval:
        await _send_email(
            db,
            school,
            _OFFICE_EDITED_SUBJECT,
            "notify_office_edited.txt",
            context | {"name": school.display_name},
        )


_TUTOR_EDITED_SUBJECT = "【業務連絡表】差戻し中の報告書が講師により修正されました"


def _last_return_actor(db: Session, report: WorkReport) -> User | None:
    """報告書を直近で講師へ差戻した操作者（学校／事務など）を返す。差戻し履歴が無ければ None。"""
    event = db.scalars(
        select(WorkReportEvent)
        .where(
            WorkReportEvent.report_id == report.id,
            WorkReportEvent.action == WorkAction.RETURN,
            WorkReportEvent.to_status == WorkStatus.RETURNED_TO_TUTOR,
        )
        .order_by(WorkReportEvent.created_at.desc())
    ).first()
    return event.actor if event and event.actor else None


async def send_tutor_edit_notification(
    db: Session,
    report: WorkReport,
    actor: User,
    changes: list[tuple[str, str, str]] | None = None,
) -> None:
    """差戻し中の報告書を講師が修正・保存した際に、差戻した操作者へ差分を1通通知する。
    事務修正通知(send_office_edit_notification)と対になる、講師→運営方向の通知。
    差戻した操作者が特定できない（履歴なし／退会済み）場合は送らない。"""
    recipient = _last_return_actor(db, report)
    if not recipient:
        return
    context = {
        "base_url": _base_url(),
        "target_month": report.target_month,
        "student_name": _student_name(report),
        "actor_name": actor.display_name,
        "changes": _format_office_changes(changes),
        "name": recipient.display_name,
    }
    await _send_email(db, recipient, _TUTOR_EDITED_SUBJECT, "notify_tutor_edited.txt", context)
