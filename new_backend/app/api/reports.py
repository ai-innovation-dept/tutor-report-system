import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_active_role, get_current_user, require_role
from app.models.shared import Assignment, User
from app.models.work import WorkReport, WorkReportEvent
from app.schemas.reports import ReportCreate, ReportEventOut, ReportOut, ReportPatch, WorkflowAction
from app.services.report_service import (
    assert_tutor_owns,
    create_report,
    get_report_or_404,
    list_reports_for_role,
    list_reports_for_tutor,
    update_report_data,
)
from app.workflow.engine import apply_transition
from app.workflow.exceptions import CommentRequired, InvalidTransition, PermissionDenied

router = APIRouter(prefix="/api/w/reports", tags=["work-reports"])


def _get_assignment(db: Session, assignment_id: uuid.UUID) -> Assignment:
    a = db.get(Assignment, assignment_id)
    if not a or not a.is_active:
        raise HTTPException(status_code=404, detail="assignment not found")
    return a


@router.post("", response_model=ReportOut, status_code=201)
def create(
    payload: ReportCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    assignment = _get_assignment(db, payload.assignment_id)
    if assignment.tutor_id != user.id:
        raise HTTPException(status_code=403, detail="not your assignment")
    try:
        report = create_report(db, assignment, user, payload.target_month, payload.form_type, payload.form_data)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="report for this assignment and month already exists")
    db.refresh(report)
    return report


@router.get("", response_model=list[ReportOut])
def list_reports(
    target_month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    if "tutor" in roles:
        return list_reports_for_tutor(db, user.id, target_month)
    return list_reports_for_role(db, active_role, target_month)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_report_or_404(db, report_id)


@router.patch("/{report_id}", response_model=ReportOut)
def patch_report(
    report_id: uuid.UUID,
    payload: ReportPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor", "sales")),
):
    report = get_report_or_404(db, report_id)
    if "tutor" in (list(user.roles or []) or [user.role]):
        assert_tutor_owns(report, user)
    update_report_data(db, report, payload.form_data)
    db.commit()
    db.refresh(report)
    return report


@router.post("/{report_id}/action", response_model=ReportOut)
def workflow_action(
    report_id: uuid.UUID,
    payload: WorkflowAction,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    report = get_report_or_404(db, report_id)
    try:
        apply_transition(db, report, user, payload.action, active_role, payload.comment)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CommentRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    db.refresh(report)
    return report


@router.get("/{report_id}/events", response_model=list[ReportEventOut])
def get_events(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    events = db.scalars(
        select(WorkReportEvent)
        .where(WorkReportEvent.report_id == report_id)
        .order_by(WorkReportEvent.created_at)
    ).all()
    return list(events)


@router.get("/{report_id}/export")
def export_pdf(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from urllib.parse import quote
    from app.services.export_service import build_report_pdf

    report = get_report_or_404(db, report_id)
    assignment = db.get(Assignment, report.assignment_id)
    student_name = assignment.student_name if assignment else "生徒"
    tutor = db.get(User, report.tutor_id)
    tutor_name = tutor.display_name if tutor else "講師"

    year, month_str = report.target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"
    filename = f"指導実績_{student_name}_{month_label}.pdf"

    content = build_report_pdf(report, student_name, tutor_name)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )
