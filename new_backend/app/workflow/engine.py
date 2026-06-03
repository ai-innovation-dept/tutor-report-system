"""
ワークフローエンジン。
遷移ルールは definitions.TRANSITIONS のみを参照する。
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.shared import User
from app.models.work import WorkReport, WorkReportEvent
from .definitions import WorkAction, find_transition
from .exceptions import CommentRequired, InvalidTransition, PermissionDenied


def apply_transition(
    db: Session,
    report: WorkReport,
    actor: User,
    action: str,
    actor_role: str,
    comment: str | None = None,
) -> WorkReport:
    """
    報告書にアクションを適用し、ステータスを遷移させる。
    成功時: report を更新して WorkReportEvent を追加、commit は呼び出し元の責任。
    失敗時: InvalidTransition / PermissionDenied / CommentRequired を送出。
    """
    transition = find_transition(report.status, action, actor_role)

    if transition is None:
        if not any(
            actor_role in t.allowed_roles
            for t in []
        ):
            pass
        from app.workflow.definitions import _INDEX
        valid_actions = {t.action for key, ts in _INDEX.items() if key[0] == report.status for t in ts}
        role_mismatch = action in valid_actions
        if role_mismatch:
            raise PermissionDenied(
                f"role '{actor_role}' is not allowed to perform '{action}' on status '{report.status}'"
            )
        raise InvalidTransition(
            f"action '{action}' is not valid from status '{report.status}'"
        )

    if transition.comment_required and not (comment and comment.strip()):
        raise CommentRequired("comment is required for this action")

    from_status = report.status
    report.status = transition.to_status
    report.current_approver_role = transition.next_approver_role
    report.updated_at = datetime.now(timezone.utc)

    if action == WorkAction.SUBMIT and from_status in ("draft", "returned_to_tutor", "returned_to_office"):
        report.submitted_at = datetime.now(timezone.utc)

    event = WorkReportEvent(
        report_id=report.id,
        actor_id=actor.id,
        action=action,
        from_status=from_status,
        to_status=transition.to_status,
        comment=comment,
    )
    db.add(event)
    return report
