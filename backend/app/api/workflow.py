# === Phase 5: 承認ワークフロー START ===
from uuid import UUID
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_report_for_user
from app.models import LessonReport, ReportAction, ReportEvent, ReportStatus, User
from app.schemas import AdminBulkReturnIn, BulkReturnIn, BulkSubmitIn, CommentIn, ReportOut
from app.services.workflow_service import auto_submit_to_admin, send_transition_notifications, transition

router = APIRouter(prefix="/api/reports", tags=["workflow"])


async def _run(report_id: UUID, action: str, payload: CommentIn, db: Session, user: User):
    report = get_report_for_user(report_id, user, db)
    transition(db, report, user, action, payload.comment)
    db.commit()
    db.refresh(report)
    await send_transition_notifications(db, action, [report], user, payload.comment)
    return report


async def _approve_and_submit_reports(reports: list[LessonReport], db: Session, user: User) -> list[LessonReport]:
    for report in reports:
        transition(db, report, user, ReportAction.parent_approve.value)
    auto_submit_to_admin(db, reports, user)
    db.commit()
    for report in reports:
        db.refresh(report)
    await send_transition_notifications(db, ReportAction.parent_approve.value, reports, user)
    await send_transition_notifications(db, ReportAction.submit_to_admin.value, reports, user)
    return reports


@router.post("/{report_id}/submit-to-parent", response_model=ReportOut)
async def submit_to_parent(report_id: UUID, payload: CommentIn = CommentIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "parent":
        return _cancel_parent_return(report_id, db, user)
    return await _run(report_id, ReportAction.submit_to_parent.value, payload, db, user)


@router.post("/{report_id}/parent-approve", response_model=ReportOut)
async def parent_approve(report_id: UUID, payload: CommentIn = CommentIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = get_report_for_user(report_id, user, db)
    return (await _approve_and_submit_reports([report], db, user))[0]


@router.post("/{report_id}/parent-return", response_model=ReportOut)
async def parent_return(report_id: UUID, payload: CommentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not payload.comment or not payload.comment.strip():
        raise HTTPException(status_code=422, detail="comment is required")
    payload.comment = payload.comment.strip()
    return await _run(report_id, ReportAction.parent_return.value, payload, db, user)


@router.post("/{report_id}/submit-to-admin", response_model=ReportOut)
async def submit_to_admin(report_id: UUID, payload: CommentIn = CommentIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.submit_to_admin.value, payload, db, user)


@router.post("/submit-to-admin-bulk")
async def submit_to_admin_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reports = _bulk_reports(payload, db, user)
    _validate_bulk(reports, user_id=user.id, owner_attr="tutor_id", status=ReportStatus.parent_approved.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.submit_to_admin.value)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.submit_to_admin.value, reports, user)
    return {"updated": changed}


@router.post("/submit-to-parent-bulk")
async def submit_to_parent_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_statuses(
        reports,
        user_id=user.id,
        owner_attr="tutor_id",
        statuses={ReportStatus.draft.value, ReportStatus.returned_to_tutor.value},
    )
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.submit_to_parent.value)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.submit_to_parent.value, reports, user)
    return {"updated": changed}


@router.post("/parent-approve-bulk")
async def parent_approve_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reports = _bulk_reports(payload, db, user)
    _validate_bulk(reports, user_id=user.id, owner_attr="parent_id", status=ReportStatus.awaiting_parent_approval.value)
    _validate_target_month(reports, payload.target_month)
    changed = [report.id for report in await _approve_and_submit_reports(reports, db, user)]
    return {"updated": changed}


@router.post("/parent-return-bulk")
async def parent_return_bulk(payload: BulkReturnIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reports = _bulk_reports(payload, db, user)
    _validate_bulk(reports, user_id=user.id, owner_attr="parent_id", status=ReportStatus.awaiting_parent_approval.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.parent_return.value, payload.comment)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.parent_return.value, reports, user, payload.comment)
    return {"updated": changed}


@router.post("/admin-return-bulk")
async def admin_return_bulk(payload: AdminBulkReturnIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    action_by_role = {
        "receiver": ReportAction.return_from_receiver.value,
        "reviewer": ReportAction.return_from_reviewer.value,
        "master": ReportAction.return_from_master.value,
    }
    reports = _bulk_reports(payload, db, user)
    _validate_target_month(reports, payload.target_month)
    action = action_by_role[payload.from_role]
    changed = []
    for report in reports:
        transition(db, report, user, action, payload.comment)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, action, reports, user, payload.comment)
    return {"updated": changed}


@router.post("/admin-receive-bulk")
async def admin_receive_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role not in {"admin_receiver", "admin_master"}:
        raise HTTPException(status_code=403, detail="action not allowed for role")
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_status(reports, ReportStatus.submitted_to_admin.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.receive.value)
        changed.append(report.id)
    db.commit()
    return {"updated": changed}


@router.post("/admin-review-bulk")
async def admin_review_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role not in {"admin_reviewer", "admin_master"}:
        raise HTTPException(status_code=403, detail="action not allowed for role")
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_status(reports, ReportStatus.received.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.re_review.value)
        changed.append(report.id)
    db.commit()
    return {"updated": changed}


@router.post("/admin-approve-bulk")
async def admin_approve_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "admin_master":
        raise HTTPException(status_code=403, detail="action not allowed for role")
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_status(reports, ReportStatus.re_reviewed.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.admin_approve.value)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.admin_approve.value, reports, user)
    return {"updated": changed}


def _bulk_reports(payload: BulkSubmitIn, db: Session, user: User) -> list[LessonReport]:
    if not payload.report_ids:
        raise HTTPException(status_code=400, detail="report_ids is required")
    reports = [get_report_for_user(report_id, user, db) for report_id in payload.report_ids]
    if len({report.id for report in reports}) != len(payload.report_ids):
        raise HTTPException(status_code=400, detail="duplicate reports are not allowed")
    return reports


def _cancel_parent_return(report_id: UUID, db: Session, user: User) -> LessonReport:
    report = get_report_for_user(report_id, user, db)
    if report.parent_id != user.id:
        raise HTTPException(status_code=403, detail="report access denied")
    if report.status != ReportStatus.returned_to_tutor.value:
        raise HTTPException(status_code=409, detail=f"invalid transition from {report.status}")
    old_status = report.status
    report.status = ReportStatus.awaiting_parent_approval.value
    report.submitted_to_parent_at = datetime.now(timezone.utc)
    db.add(
        ReportEvent(
            report_id=report.id,
            actor_id=user.id,
            action="parent_return_cancel",
            from_status=old_status,
            to_status=ReportStatus.awaiting_parent_approval.value,
        )
    )
    db.commit()
    db.refresh(report)
    return report


def _validate_bulk(reports: list[LessonReport], user_id, owner_attr: str, status: str) -> None:
    _validate_bulk_statuses(reports, user_id=user_id, owner_attr=owner_attr, statuses={status})


def _validate_bulk_statuses(reports: list[LessonReport], user_id, owner_attr: str, statuses: set[str]) -> None:
    owner_values = {getattr(report, owner_attr) for report in reports}
    months = {report.target_month for report in reports}
    report_statuses = {report.status for report in reports}
    if owner_values != {user_id}:
        raise HTTPException(status_code=403, detail="bulk reports must belong to the current user")
    if len(months) != 1:
        raise HTTPException(status_code=409, detail="bulk reports must be in the same month")
    if not report_statuses.issubset(statuses):
        allowed = ", ".join(sorted(statuses))
        raise HTTPException(status_code=409, detail=f"bulk reports must all be one of: {allowed}")


def _validate_bulk_status(reports: list[LessonReport], status: str) -> None:
    statuses = {report.status for report in reports}
    if statuses != {status}:
        raise HTTPException(status_code=409, detail=f"bulk reports must all be {status}")


def _validate_target_month(reports: list[LessonReport], target_month: str | None) -> None:
    if target_month and {report.target_month for report in reports} != {target_month}:
        raise HTTPException(status_code=409, detail="target_month does not match reports")


@router.post("/{report_id}/receive", response_model=ReportOut)
async def receive(report_id: UUID, payload: CommentIn = CommentIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.receive.value, payload, db, user)


@router.post("/{report_id}/return-from-receiver", response_model=ReportOut)
async def return_from_receiver(report_id: UUID, payload: CommentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.return_from_receiver.value, payload, db, user)


@router.post("/{report_id}/re-review", response_model=ReportOut)
async def re_review(report_id: UUID, payload: CommentIn = CommentIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.re_review.value, payload, db, user)


@router.post("/{report_id}/return-from-reviewer", response_model=ReportOut)
async def return_from_reviewer(report_id: UUID, payload: CommentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.return_from_reviewer.value, payload, db, user)


@router.post("/{report_id}/admin-approve", response_model=ReportOut)
async def admin_approve(report_id: UUID, payload: CommentIn = CommentIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.admin_approve.value, payload, db, user)


@router.post("/{report_id}/return-from-master", response_model=ReportOut)
async def return_from_master(report_id: UUID, payload: CommentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return await _run(report_id, ReportAction.return_from_master.value, payload, db, user)
# === Phase 5 END ===
