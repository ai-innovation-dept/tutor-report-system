"""ワークフロー遷移のサービス層。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import has_role
from app.models.shared import User
from app.models.work import WorkReport, WorkReportEvent
from app.services.notification_service import _enqueue_notification
from app.workflow.engine import apply_transition
from app.workflow.definitions import WorkStatus
from app.workflow.exceptions import PermissionDenied


def _assert_duty_separation(
    db: Session,
    report: WorkReport,
    actor: User,
    actor_role: str,
) -> None:
    """事務（office）と営業（sales）を兼務するスタッフは同一報告の両ステップを処理できない。

    事務ステップ（AWAITING_OFFICE / RETURNED_TO_OFFICE）を処理済みの場合、
    同じ人が営業ステップ（AWAITING_SALES → AWAITING_FINANCE）を処理することを禁止する。
    """
    if actor_role != "sales":
        return
    if not (has_role(actor, "office") and has_role(actor, "sales")):
        return

    # 事務ステップを既にこのアクターが処理したか確認
    office_events = db.scalars(
        select(WorkReportEvent).where(
            WorkReportEvent.report_id == report.id,
            WorkReportEvent.actor_id == actor.id,
            WorkReportEvent.from_status.in_([WorkStatus.AWAITING_OFFICE, WorkStatus.RETURNED_TO_OFFICE]),
        )
    ).all()

    if office_events:
        raise PermissionDenied(
            "事務・営業兼務スタッフは同一報告の事務処理と営業確認の両方を行うことはできません"
        )


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
    _assert_duty_separation(db, report, actor, actor_role)
    from_status = report.status
    apply_transition(db, report, actor, action, actor_role, comment)
    _enqueue_notification(db, report, action, from_status, actor)
    db.flush()
    return report
