"""ワークフロー遷移のサービス層。"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies.auth import has_role
from app.models.shared import User
from app.models.work import WorkReport, WorkReportEvent
from app.services.notification_service import _enqueue_notification
from app.workflow.engine import apply_transition
from app.workflow.definitions import WorkAction, WorkStatus
from app.workflow.exceptions import PermissionDenied


# 事務承認とみなす遷移元ステータス（事務確認待ち・事務差戻し中からの承認）
_OFFICE_FROM_STATUSES = (WorkStatus.AWAITING_OFFICE, WorkStatus.RETURNED_TO_OFFICE)


def _is_separation_exempt(actor: User) -> bool:
    # 管理者・管理責任者は職務分掌の対象外
    return has_role(actor, "admin_master") or has_role(actor, "admin_chief")


def _is_office_sales_dual(actor: User) -> bool:
    return has_role(actor, "office") and has_role(actor, "sales")


def _tutor_ids_acted_office(db: Session, actor_id) -> set:
    """actor が事務承認（事務確認待ち/事務差戻しからの承認）を行った講師IDの集合を返す。"""
    rows = db.scalars(
        select(WorkReport.tutor_id)
        .join(WorkReportEvent, WorkReportEvent.report_id == WorkReport.id)
        .where(
            WorkReportEvent.actor_id == actor_id,
            WorkReportEvent.action == WorkAction.APPROVE,
            WorkReportEvent.from_status.in_(_OFFICE_FROM_STATUSES),
        )
        .distinct()
    ).all()
    return set(rows)


def _tutor_ids_acted_sales(db: Session, actor_id) -> set:
    """actor が営業承認（営業確認待ちからの承認）を行った講師IDの集合を返す。"""
    rows = db.scalars(
        select(WorkReport.tutor_id)
        .join(WorkReportEvent, WorkReportEvent.report_id == WorkReport.id)
        .where(
            WorkReportEvent.actor_id == actor_id,
            WorkReportEvent.action == WorkAction.APPROVE,
            WorkReportEvent.from_status == WorkStatus.AWAITING_SALES,
        )
        .distinct()
    ).all()
    return set(rows)


def _assert_duty_separation(
    db: Session,
    report: WorkReport,
    actor: User,
    actor_role: str,
    action: str,
) -> None:
    """事務（office）と営業（sales）を兼務するスタッフは、同一講師に対して
    事務工程と営業工程の両方の判断（承認・差戻し）を行うことはできない（どちらか一方のみ）。

    承認だけでなく差戻しも工程上の判断のため対象とする。
    判定は講師単位・全期間で永続する（既存システムの受付/再鑑の職務分掌と同じ）。
    管理者・管理責任者は対象外。
    """
    if action not in (WorkAction.APPROVE, WorkAction.RETURN):
        return
    if _is_separation_exempt(actor) or not _is_office_sales_dual(actor):
        return

    if actor_role == "office" and report.status in _OFFICE_FROM_STATUSES:
        if report.tutor_id in _tutor_ids_acted_sales(db, actor.id):
            raise PermissionDenied(
                "この講師はあなたが営業承認を担当済みのため、事務での承認・差戻しはできません"
                "（事務と営業は同一講師で兼務できません）。"
            )
    elif actor_role == "sales" and report.status == WorkStatus.AWAITING_SALES:
        if report.tutor_id in _tutor_ids_acted_office(db, actor.id):
            raise PermissionDenied(
                "この講師はあなたが事務承認を担当済みのため、営業での承認・差戻しはできません"
                "（事務と営業は同一講師で兼務できません）。"
            )


def separation_locks(db: Session, actor: User) -> dict[str, list[str]]:
    """UI制御用：兼務スタッフが事務承認/営業承認を担当済みの講師ID一覧を返す。

    事務承認済みの講師は営業承認ボタンを、営業承認済みの講師は事務承認ボタンを
    無効化するために使う。兼務でないユーザー・管理者・管理責任者は対象外のため空。
    """
    if _is_separation_exempt(actor) or not _is_office_sales_dual(actor):
        return {"office_tutor_ids": [], "sales_tutor_ids": []}
    return {
        "office_tutor_ids": [str(t) for t in _tutor_ids_acted_office(db, actor.id)],
        "sales_tutor_ids": [str(t) for t in _tutor_ids_acted_sales(db, actor.id)],
    }


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
    _assert_duty_separation(db, report, actor, actor_role, action)
    from_status = report.status
    apply_transition(db, report, actor, action, actor_role, comment)
    _enqueue_notification(db, report, action, from_status, actor)
    db.flush()
    return report
