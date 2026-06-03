"""ワークフロー遷移のサービス層。"""
from sqlalchemy.orm import Session

from app.models.shared import User
from app.models.work import WorkReport
from app.services.notification_service import _enqueue_notification
from app.workflow.engine import apply_transition


def execute_transition(
    db: Session,
    report: WorkReport,
    actor: User,
    actor_role: str,
    action: str,
    comment: str | None,
) -> WorkReport:
    """
    ワークフロー遷移を実行し、対応する通知レコードを作成する。

    WorkReportEvent は engine.apply_transition() が追加するため、ここでは追加しない。
    commit は API 層の責任。
    """
    from_status = report.status
    apply_transition(db, report, actor, action, actor_role, comment)
    _enqueue_notification(db, report, action, from_status, actor)
    db.flush()
    return report
