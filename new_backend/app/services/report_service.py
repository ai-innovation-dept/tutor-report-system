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
