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
from app.services.lesson_time import duration_label, teaching_minutes
from app.services.notification_service import enqueue, send_email_notification

logger = getLogger(__name__)

APPROVAL_REQUEST_SUBJECT = "【指導実績】承認依頼が届きました"
RETURNED_SUBJECT = "【指導実績】差戻しコメントが届きました"
PARENT_APPROVED_SUBJECT = "【指導実績】保護者が承認しました"
SUBMITTED_TO_ADMIN_SUBJECT = "【指導実績】報告書が提出されました"
ADMIN_RETURN_SUBJECT = "【指導実績】運営から差戻しがありました"
ADMIN_APPROVED_SUBJECT = "【指導実績】最終承認が完了しました"
REPORT_MODIFIED_SUBJECT = "【指導実績】報告書が修正されました"
TUTOR_EDIT_SUBJECT = "【指導実績】差戻し中の報告書が講師により修正されました"


# 承認フロー: 講師→保護者→受付→再鑑（再鑑承認が最終承認）。
# 管理者(admin_master)・管理責任者(admin_chief)は承認フローから外れ、閲覧・PDF・未処理クローズのみ。
# 旧フローの admin_approve / return_from_master アクションは廃止（ReportAction の値は
# 過去の report_events の履歴表示のため残す）。
TRANSITIONS = {
    ReportAction.submit_to_parent.value: ("tutor", [ReportStatus.draft.value, ReportStatus.returned_to_tutor.value], ReportStatus.awaiting_parent_approval.value, "submitted_to_parent_at"),
    ReportAction.parent_approve.value: ("parent", [ReportStatus.awaiting_parent_approval.value], ReportStatus.parent_approved.value, "parent_approved_at"),
    ReportAction.parent_return.value: ("parent", [ReportStatus.awaiting_parent_approval.value], ReportStatus.returned_to_tutor.value, None),
    ReportAction.submit_to_admin.value: ("tutor", [ReportStatus.parent_approved.value], ReportStatus.submitted_to_admin.value, "submitted_to_admin_at"),
    ReportAction.receive.value: ("admin_receiver", [ReportStatus.submitted_to_admin.value, ReportStatus.returned_to_receiver.value], ReportStatus.received.value, "received_at"),
    ReportAction.return_from_receiver.value: ("admin_receiver", [ReportStatus.submitted_to_admin.value, ReportStatus.received.value, ReportStatus.returned_to_receiver.value], ReportStatus.returned_to_tutor.value, None),
    # 再鑑承認＝最終承認。旧フローで最終承認待ち(re_reviewed)のまま残った報告書も再鑑者が最終化できる。
    ReportAction.re_review.value: ("admin_reviewer", [ReportStatus.received.value, ReportStatus.re_reviewed.value], ReportStatus.admin_approved.value, "re_reviewed_at"),
    # 完了(admin_approved)後の差戻しは最終承認者である再鑑者が受付へ行う。
    ReportAction.return_from_reviewer.value: ("admin_reviewer", [ReportStatus.received.value, ReportStatus.re_reviewed.value, ReportStatus.admin_approved.value], ReportStatus.returned_to_receiver.value, None),
}

# 差戻し系アクション。差戻しコメントをチャットへ転記し、「直近の差戻し元」の判定にも使う。
# 差戻し要求の許可(approve_return_request)も講師へ差戻る操作のため差戻し扱いにする。
RETURN_ACTIONS = {
    ReportAction.parent_return.value,
    ReportAction.return_from_receiver.value,
    ReportAction.return_from_reviewer.value,
    ReportAction.return_from_master.value,
    ReportAction.approve_return_request.value,
}

# ---------------------------------------------------------------------------
# 講師起点の差戻し要求（改修依頼 202607211144・新システム EMPS の同機能を移植）
# ---------------------------------------------------------------------------
# 講師は「差戻しを要求」（理由必須）するだけで、実行するのはその時点でボールを持つ承認担当。
#   許可(approve_return_request) → 講師へ差戻し（returned_to_tutor）
#   却下(decline_return_request・理由必須) → ステータスは変えず要求のみ解消（講師は再要求できる）
# 要求は承認等でボールが移っても未解決のまま新しいボール保持ロールへ引き継がれる。
# 未解決かどうかは report_events から導出するため、DBカラム／マイグレーションは不要。
# 値: 対象ステータス → そのステータスでボールを持つ（許可・却下できる）ロール。
# ※ draft/returned_to_tutor は講師自身が持つ、parent_approved は講師が運営へ提出する工程、
#   closed は終了済みのため、いずれも要求の対象外。
RETURN_REQUEST_BALL_HOLDERS = {
    ReportStatus.awaiting_parent_approval.value: "parent",
    ReportStatus.submitted_to_admin.value: "admin_receiver",
    ReportStatus.returned_to_receiver.value: "admin_receiver",
    ReportStatus.received.value: "admin_reviewer",
    ReportStatus.re_reviewed.value: "admin_reviewer",
    ReportStatus.admin_approved.value: "admin_reviewer",
}

REQUEST_RETURN_ACTIONS = {
    ReportAction.request_return.value,
    ReportAction.approve_return_request.value,
    ReportAction.decline_return_request.value,
}

# 理由（コメント）の入力を必須とするアクション。差戻し要求の許可は要求理由を自動転記するため対象外。
COMMENT_REQUIRED_ACTIONS = (RETURN_ACTIONS - {ReportAction.approve_return_request.value}) | {
    ReportAction.request_return.value,
    ReportAction.decline_return_request.value,
}


def return_request_state(events) -> tuple[ReportEvent | None, ReportEvent | None]:
    """差戻し要求の現況を (未解決の要求イベント, 直近の却下イベント) で返す。

    作成日時の昇順に並んだイベント列を新しい順に走査し、最初に見つかった要求関連イベントで判定する。
    - request_return → 未解決（承認等でボールが移っても引き継がれる）
    - decline_return_request → 却下済み（講師は再要求できる）
    - 許可・講師へ戻る差戻し・クローズ → 解決済み（どちらも None）
    ※ 新システムの WorkReport._return_request_state（new_backend/app/models/work.py）と同一仕様。
      片方だけ変更しないこと。
    """
    for event in reversed(list(events)):
        if event.action == ReportAction.request_return.value:
            return event, None
        if event.action == ReportAction.decline_return_request.value:
            return None, event
        if event.action == ReportAction.approve_return_request.value or event.to_status in {
            ReportStatus.returned_to_tutor.value,
            ReportStatus.closed.value,
        }:
            return None, None
    return None, None


def load_return_request_state(db: Session, report: LessonReport) -> tuple[ReportEvent | None, ReportEvent | None]:
    """DBからイベントを読み出して `return_request_state` を評価する（サーバ側ガード用）。"""
    events = db.scalars(
        select(ReportEvent).where(ReportEvent.report_id == report.id).order_by(ReportEvent.created_at)
    ).all()
    return return_request_state(events)


def _resolve_rule(report: LessonReport, action: str):
    """アクションの遷移ルール (必要ロール, 許可元ステータス, 遷移先, 記録する日時カラム) を返す。

    差戻し要求系は対応ロールがステータスごとに変わるため、通常の TRANSITIONS 表ではなく
    RETURN_REQUEST_BALL_HOLDERS からその場で組み立てる（要求・却下はステータスを変えない）。
    対象外ステータスなら None（呼び出し側で 409）。
    """
    if action in REQUEST_RETURN_ACTIONS:
        holder = RETURN_REQUEST_BALL_HOLDERS.get(report.status)
        if holder is None:
            return None
        if action == ReportAction.request_return.value:
            return ("tutor", [report.status], report.status, None)
        if action == ReportAction.approve_return_request.value:
            return (holder, [report.status], ReportStatus.returned_to_tutor.value, None)
        return (holder, [report.status], report.status, None)
    return TRANSITIONS.get(action)


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
# 差戻し要求への対応（許可・却下）も工程上の判断のため、承認・差戻しと同じ職務分掌を適用する。
# キー＝同一報告書での担当済みアクション、値＝そのとき出すメッセージ。
_REQUEST_SEPARATION_MESSAGE = {
    ReportAction.receive.value: "この報告書はあなたが受付承認を担当済みのため、差戻し要求への対応はできません（受付と再鑑は同一報告書で同一人物が兼務できません）。",
    ReportAction.re_review.value: "この報告書はあなたが再鑑承認を担当済みのため、差戻し要求への対応はできません（受付と再鑑は同一報告書で同一人物が兼務できません）。",
}


def _role_allowed(required: str, actor: User) -> bool:
    # 管理者・管理責任者は承認フロー外のため、かつての「admin_*を代行できる」特例は廃止。
    return has_role(actor, required)


def _reports_acted_by(db: Session, actor_id, action: str) -> set:
    """actor が指定アクション(receive/re_review)を実施した報告書IDの集合を返す。"""
    rows = db.scalars(
        select(ReportEvent.report_id)
        .where(ReportEvent.actor_id == actor_id, ReportEvent.action == action)
        .distinct()
    ).all()
    return set(rows)


def _conflicting_action(action: str, required_role: str | None) -> str | None:
    """そのアクションを不可にする「同一報告書での担当済み」承認アクションを返す。

    差戻し要求への対応（許可・却下）は、対応者がどちらの工程のボールを持っているかで衝突相手が変わる
    （受付が対応するなら再鑑担当済みで不可・再鑑が対応するなら受付担当済みで不可）。
    """
    if action in {ReportAction.approve_return_request.value, ReportAction.decline_return_request.value}:
        if required_role == "admin_receiver":
            return ReportAction.re_review.value
        if required_role == "admin_reviewer":
            return ReportAction.receive.value
        return None  # 保護者の対応は職務分掌の対象外
    return SEPARATION_CONFLICT.get(action)


def _assert_separation_of_duties(
    db: Session, report: LessonReport, actor: User, action: str, required_role: str | None = None
) -> None:
    conflicting = _conflicting_action(action, required_role)
    if not conflicting or has_role(actor, "admin_master") or has_role(actor, "admin_chief"):
        return
    if report.id in _reports_acted_by(db, actor.id, conflicting):
        message = (
            _REQUEST_SEPARATION_MESSAGE[conflicting]
            if action in {ReportAction.approve_return_request.value, ReportAction.decline_return_request.value}
            else _SEPARATION_MESSAGE[action]
        )
        raise HTTPException(status_code=409, detail=message)


def separation_locks(db: Session, actor: User) -> dict[str, list[str]]:
    """UI用：現在のユーザーが受付/再鑑を担当済みの報告書ID一覧。admin_master / admin_chief は対象外のため空。"""
    if has_role(actor, "admin_master") or has_role(actor, "admin_chief"):
        return {"received_report_ids": [], "reviewed_report_ids": []}
    return {
        "received_report_ids": [str(rid) for rid in _reports_acted_by(db, actor.id, ReportAction.receive.value)],
        "reviewed_report_ids": [str(rid) for rid in _reports_acted_by(db, actor.id, ReportAction.re_review.value)],
    }


def _check_return_request(db: Session, report: LessonReport, action: str, comment: str | None) -> str | None:
    """差戻し要求のガードを適用し、許可アクションのコメント（要求理由の転記）を組み立てて返す。

    - 要求の二重受付を防ぐ（未解決の要求があるあいだは再要求できない）
    - 許可・却下は未解決の要求がある場合のみ可能
    - 許可は、講師画面の差戻し理由欄・履歴が単体で読めるよう要求理由を自動転記する
      （承認担当のコメントは任意。EMPS の engine.apply_transition と同一仕様）
    """
    pending, _ = load_return_request_state(db, report)
    if action == ReportAction.request_return.value:
        if pending is not None:
            raise HTTPException(status_code=409, detail="差戻し要求は受付済みです。承認担当の対応をお待ちください")
        return comment
    if pending is None:
        raise HTTPException(status_code=409, detail="未対応の差戻し要求がありません")
    if action == ReportAction.decline_return_request.value:
        return comment
    base_comment = comment.strip() if (comment and comment.strip()) else "講師の差戻し要求を許可しました。"
    return f"{base_comment}\n【講師の差戻し要求】{pending.comment}" if pending.comment else base_comment


def transition(db: Session, report: LessonReport, actor: User, action: str, comment: str | None = None) -> LessonReport:
    rule = _resolve_rule(report, action)
    if not rule:
        if action in REQUEST_RETURN_ACTIONS:
            # 差戻し要求の対象外ステータス（講師が持っている・クローズ済みなど）
            raise HTTPException(status_code=409, detail=f"invalid transition from {report.status}")
        raise HTTPException(status_code=400, detail="unknown action")
    required_role, allowed_from, to_status, timestamp_field = rule
    if not _role_allowed(required_role, actor):
        raise HTTPException(status_code=403, detail="action not allowed for role")
    if action in REQUEST_RETURN_ACTIONS:
        comment = _check_return_request(db, report, action, comment)
    if action in COMMENT_REQUIRED_ACTIONS and not comment:
        raise HTTPException(status_code=400, detail="return comment is required")
    if report.status not in allowed_from:
        raise HTTPException(status_code=409, detail=f"invalid transition from {report.status}")
    _assert_separation_of_duties(db, report, actor, action, required_role)
    skip_parent_approval = action == ReportAction.submit_to_parent.value and bool(report.parent and report.parent.skip_parent_approval)
    if skip_parent_approval:
        to_status = ReportStatus.submitted_to_admin.value
        timestamp_field = "submitted_to_admin_at"

    old_status = report.status
    report.status = to_status
    if timestamp_field:
        setattr(report, timestamp_field, datetime.now(timezone.utc))
    if action == ReportAction.re_review.value:
        # 再鑑承認＝最終承認のため、最終承認時刻も同時に記録する（帳票・画面の最終承認日時表示用）
        report.admin_approved_at = datetime.now(timezone.utc)
    db.add(ReportEvent(report_id=report.id, actor_id=actor.id, action=action, from_status=old_status, to_status=to_status, comment=comment))
    if action in RETURN_ACTIONS and comment:
        db.add(ChatMessage(report_id=report.id, sender_id=actor.id, body=f"差戻し理由: {comment}"))
    # 差戻し要求・その却下はステータスが変わらない（イベント記録のみ）ため、状態変更の通知は出さない。
    # 講師・承認担当への到達は画面のバッジ／タスク表示が担う（EMPS と同じ設計）。
    if action in {ReportAction.request_return.value, ReportAction.decline_return_request.value}:
        return report

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


def _total_minutes(reports: list[LessonReport]) -> int:
    return sum(teaching_minutes(report) for report in reports)


async def _send_email(db: Session, to_user: User | None, subject: str, template_name: str, context: dict) -> None:
    if not to_user:
        return
    # 無効化・削除済みユーザーには通知メールを送らない（無効化＝削除と同等に「宛先から外す」。
    # 行・メールアドレスは保持し、有効化で元に戻せる）。EMPS の _send_email と同じ扱い（宛先解決の唯一の関門）。
    if not to_user.is_active or to_user.deleted_at:
        logger.info("mail skipped: recipient inactive/deleted email=%s subject=%s", to_user.email, subject)
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


def _format_edit_comment(comment: str | None) -> str:
    """任意コメントを本文用ブロックに整形（無ければ空文字）。波括弧は str.format 対策でエスケープ。"""
    if not comment or not comment.strip():
        return ""
    escaped = comment.strip().replace("{", "{{").replace("}", "}}")
    return f"\n【コメント】\n{escaped}\n"


async def notify_report_modified(
    db: Session,
    sample_report: LessonReport,
    changes: list[tuple[str, str, str]],
    actor: User,
    comment: str | None = None,
) -> None:
    """受付による報告（生徒×講師×対象月）単位の修正を、講師・保護者へまとめて1通通知する。
    保護者は未設定/承認スキップなら送らない。sample_report はグループ内の任意の1件
    （生徒名・対象月・講師・保護者はグループ内で共通のため代表として使う）。
    changes は日付を含めた差分（例: 「6/15 開始時刻」）のリスト。"""
    context = {
        "base_url": _base_url(),
        "student_name": _student_name(sample_report),
        "target_month": sample_report.target_month,
        "actor_name": actor.display_name,
        "changes": _format_changes(changes) if changes else "（明細の変更はありません）",
        "comment_section": _format_edit_comment(comment),
    }
    tutor = _tutor(sample_report)
    await _send_email(
        db, tutor, REPORT_MODIFIED_SUBJECT, "notify_report_modified.txt",
        context | {"name": tutor.display_name if tutor else "講師"},
    )
    parent = _parent(sample_report)
    if parent and not parent.skip_parent_approval:
        await _send_email(
            db, parent, REPORT_MODIFIED_SUBJECT, "notify_report_modified.txt",
            context | {"name": parent.display_name},
        )


def _last_return_actor(db: Session, report: LessonReport) -> User | None:
    """報告書を直近で差戻した操作者（保護者または運営担当）を返す。差戻し履歴が無ければ None。"""
    event = db.scalars(
        select(ReportEvent)
        .where(ReportEvent.report_id == report.id, ReportEvent.action.in_(RETURN_ACTIONS))
        .order_by(ReportEvent.created_at.desc())
    ).first()
    return event.actor if event and event.actor else None


async def notify_tutor_report_edited(
    db: Session,
    report: LessonReport,
    changes: list[tuple[str, str, str]],
    tutor: User,
    comment: str | None = None,
) -> None:
    """差戻し中の報告書を講師が修正・保存したことを、差戻した操作者へ1通通知する。
    受付/事務の編集通知(notify_report_modified)と対になる、講師→運営方向の通知。
    差戻した操作者が特定できない（履歴なし／退会済み）場合は送信しない。"""
    recipient = _last_return_actor(db, report)
    if not recipient or not recipient.is_active:
        return
    context = {
        "base_url": _base_url(),
        "student_name": _student_name(report),
        "target_month": report.target_month,
        "tutor_name": tutor.display_name if tutor else "講師",
        "lesson_date": _format_lesson_date(report),
        "changes": _format_changes(changes) if changes else "（明細の変更はありません）",
        "comment_section": _format_edit_comment(comment),
        "name": recipient.display_name,
    }
    await _send_email(db, recipient, TUTOR_EDIT_SUBJECT, "notify_tutor_edited.txt", context)


async def _send_group_notification(db: Session, action: str, reports: list[LessonReport], actor: User, comment: str | None = None) -> None:
    if not reports:
        return
    report = sorted(reports, key=lambda item: (item.lesson_date, item.start_time))[0]
    count = len(reports)
    total_hours = duration_label(_total_minutes(reports))
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

    # 差戻し要求の許可＝講師への差戻し。差戻し元（保護者／運営）に応じた件名で講師へ通知する
    # （要求・却下そのものはメールを送らない＝画面のバッジ／タスク表示で到達する設計）。
    if action == ReportAction.approve_return_request.value:
        tutor = _tutor(report)
        await _send_email(
            db,
            tutor,
            RETURNED_SUBJECT if has_role(actor, "parent") else ADMIN_RETURN_SUBJECT,
            "notify_returned.txt",
            context | {
                "tutor_name": tutor.display_name if tutor else "講師",
                "actor_name": actor.display_name,
                "lesson_date": _format_lesson_date(report),
                "comment": comment or "",
            },
        )
        return

    if action == ReportAction.return_from_reviewer.value:
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

    # 再鑑承認＝最終承認のため、最終承認完了メールは再鑑承認時に送る
    if action == ReportAction.re_review.value:
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
