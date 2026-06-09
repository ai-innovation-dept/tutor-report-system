"""報告書の作成・更新・取得ロジック。"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forms.definitions import get_form
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkReport
from app.workflow.definitions import WorkStatus


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def get_report_or_404(db: Session, report_id) -> WorkReport:
    report = db.get(WorkReport, report_id)
    if not report:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="report not found")
    return report


def assert_tutor_owns(report: WorkReport, user: User) -> None:
    if report.tutor_id != user.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="not your report")


def create_report(
    db: Session,
    assignment: Assignment,
    tutor: User,
    target_month: str,
    form_type: str,
    form_data: dict,
) -> WorkReport:
    get_form(form_type)  # validates form_type exists
    report = WorkReport(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        target_month=target_month,
        form_type=form_type,
        form_data=form_data,
        status=WorkStatus.DRAFT,
        current_approver_role="tutor",
    )
    db.add(report)
    db.flush()
    return report


def update_report_data(db: Session, report: WorkReport, form_data: dict) -> WorkReport:
    if report.status not in (WorkStatus.DRAFT, WorkStatus.RETURNED_TO_TUTOR, WorkStatus.RETURNED_TO_OFFICE):
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="report cannot be edited in current status")
    report.form_data = form_data
    report.updated_at = datetime.now(timezone.utc)
    return report


# 事務担当が報告書を修正できるステータス。
# 既存システムの受付(admin_receiver)の編集可能3ステータス
# （受付待ち/再鑑待ち/受付差戻し中）に対応する。
OFFICE_EDIT_STATUSES = (
    WorkStatus.AWAITING_OFFICE,
    WorkStatus.AWAITING_SALES,
    WorkStatus.RETURNED_TO_OFFICE,
)


def office_update_report_data(db: Session, report: WorkReport, form_data: dict) -> WorkReport:
    """事務担当による報告書修正。講師の編集フローとは別系統で、再承認は不要・通知のみ。"""
    if report.status not in OFFICE_EDIT_STATUSES:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail="事務が修正できるのは、事務確認待ち・営業確認待ち・事務差戻し中の報告書のみです",
        )
    report.form_data = form_data
    report.updated_at = datetime.now(timezone.utc)
    return report


def list_reports_for_tutor(db: Session, tutor_id, target_month: str | None = None) -> list[WorkReport]:
    stmt = select(WorkReport).where(WorkReport.tutor_id == tutor_id)
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return list(db.scalars(stmt.order_by(WorkReport.target_month.desc())))


def list_reports_for_role(db: Session, role: str, target_month: str | None = None) -> list[WorkReport]:
    stmt = select(WorkReport).where(WorkReport.current_approver_role == role)
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return list(db.scalars(stmt.order_by(WorkReport.target_month.desc(), WorkReport.created_at)))


def list_reports_for_school(db: Session, school_user_id, target_month: str | None = None) -> list[WorkReport]:
    """学校ユーザーが担当する紐付け（assignment.parent_id）の報告を全ステータスで返す。"""
    stmt = (
        select(WorkReport)
        .join(Assignment, Assignment.id == WorkReport.assignment_id)
        .where(Assignment.parent_id == school_user_id)
    )
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return list(db.scalars(stmt.order_by(WorkReport.target_month.desc(), WorkReport.created_at)))
