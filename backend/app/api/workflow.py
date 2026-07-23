# === Phase 5: 承認ワークフロー START ===
from uuid import UUID
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.rbac import has_role
from app.deps import get_current_user, get_report_for_user
from app.models import LessonReport, ReportAction, ReportEvent, ReportStatus, User
from app.schemas import (
    AdminBulkReturnIn,
    BulkReturnIn,
    BulkSubmitIn,
    CommentIn,
    ParentApproveBulkIn,
    ParentApproveIn,
    ReportOut,
)
from app.services.monthly_report_service import apply_parent_note, assert_monthly_reports_ready
from app.services.workflow_service import (
    RETURN_REQUEST_BALL_HOLDERS,
    auto_submit_to_admin,
    send_transition_notifications,
    transition,
)

router = APIRouter(prefix="/api/reports", tags=["workflow"])


def _assert_lesson_reports_ready(reports: list[LessonReport]) -> None:
    """承認依頼（保護者提出）の前提: 仮保存中（指導内容が未入力）の報告書がないこと（改修 202607231025 ②）。

    仮保存で content を空にできるようにした代わりに、現行の不変条件（提出済み報告は内容あり）を
    この関門で維持する。未入力があれば対象の指導日を示して 422 で差し戻す。
    """
    for report in reports:
        if not (report.content or "").strip():
            day = report.lesson_date
            raise HTTPException(
                status_code=422,
                detail=(
                    f"仮保存中の指導報告があります（{day.month}月{day.day}日分）。"
                    "指導内容（何を指導したか）を入力してから承認依頼してください。"
                ),
            )


async def _run(report_id: UUID, action: str, payload: CommentIn, db: Session, user: User):
    report = get_report_for_user(report_id, user, db)
    transition(db, report, user, action, payload.comment)
    db.commit()
    db.refresh(report)
    await send_transition_notifications(db, action, [report], user, payload.comment)
    return report


async def _approve_and_submit_reports(
    reports: list[LessonReport], db: Session, user: User, parent_note: str | None = None
) -> list[LessonReport]:
    # 指導月報がある月は保護者記入欄（ご要望/連絡事項）の入力を必須とし、承認と同時に保存する。
    # 月報が無い月（本機能リリース前に提出済みの月など）は従来どおり承認できる。
    apply_parent_note(db, reports, user, parent_note)
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
    if has_role(user, "parent"):
        return _cancel_parent_return(report_id, db, user)
    # 承認依頼（保護者への提出）は指導月報の作成（学年・問題点と対策の入力）を必須とする
    report = get_report_for_user(report_id, user, db)
    _assert_lesson_reports_ready([report])
    assert_monthly_reports_ready(db, [report])
    return await _run(report_id, ReportAction.submit_to_parent.value, payload, db, user)


@router.post("/{report_id}/parent-approve", response_model=ReportOut)
async def parent_approve(report_id: UUID, payload: ParentApproveIn = ParentApproveIn(), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = get_report_for_user(report_id, user, db)
    return (await _approve_and_submit_reports([report], db, user, payload.parent_note))[0]


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
    # 承認依頼（保護者への提出）は指導月報の作成（学年・問題点と対策の入力）を必須とする
    _assert_lesson_reports_ready(reports)
    assert_monthly_reports_ready(db, reports)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.submit_to_parent.value)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.submit_to_parent.value, reports, user)
    return {"updated": changed}


@router.post("/parent-approve-bulk")
async def parent_approve_bulk(payload: ParentApproveBulkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    reports = _bulk_reports(payload, db, user)
    _validate_bulk(reports, user_id=user.id, owner_attr="parent_id", status=ReportStatus.awaiting_parent_approval.value)
    _validate_target_month(reports, payload.target_month)
    changed = [report.id for report in await _approve_and_submit_reports(reports, db, user, payload.parent_note)]
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


# ---------------------------------------------------------------------------
# 講師起点の差戻し要求（改修依頼 202607211144）
# ---------------------------------------------------------------------------
# 画面の操作単位（担当×対象月のまとまり）に合わせ、一括APIのみを用意する。
# 対象ステータス・対応ロール・二重要求などのガードは workflow_service.transition が担う。

@router.post("/request-return-bulk")
async def request_return_bulk(payload: BulkReturnIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """講師が、現在ボールを持つ承認担当へ差戻しを要求する（理由必須・メール通知なし）。"""
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_statuses(
        reports,
        user_id=user.id,
        owner_attr="tutor_id",
        statuses=set(RETURN_REQUEST_BALL_HOLDERS),
    )
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.request_return.value, payload.comment)
        changed.append(report.id)
    db.commit()
    return {"updated": changed}


@router.post("/approve-return-request-bulk")
async def approve_return_request_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """ボールを持つ承認担当が差戻し要求を許可する（＝講師へ差戻し・要求理由は自動転記）。"""
    reports = _bulk_reports(payload, db, user)
    _validate_target_month(reports, payload.target_month)
    action = ReportAction.approve_return_request.value
    changed = []
    for report in reports:
        transition(db, report, user, action)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, action, reports, user, _last_return_comment(db, reports[0]))
    return {"updated": changed}


@router.post("/decline-return-request-bulk")
async def decline_return_request_bulk(payload: BulkReturnIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """ボールを持つ承認担当が差戻し要求を却下する（理由必須・ステータスは変わらない）。"""
    reports = _bulk_reports(payload, db, user)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.decline_return_request.value, payload.comment)
        changed.append(report.id)
    db.commit()
    return {"updated": changed}


def _last_return_comment(db: Session, report: LessonReport) -> str | None:
    """許可時に記録された差戻しコメント（要求理由の転記込み）を通知メール本文へ渡すために取り出す。"""
    event = db.scalars(
        select(ReportEvent)
        .where(ReportEvent.report_id == report.id, ReportEvent.action == ReportAction.approve_return_request.value)
        .order_by(ReportEvent.created_at.desc())
    ).first()
    return event.comment if event else None


@router.post("/admin-return-bulk")
async def admin_return_bulk(payload: AdminBulkReturnIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 管理者(master)の差戻しは廃止。完了後の差戻しは最終承認者である再鑑者(reviewer)が行う。
    action_by_role = {
        "receiver": ReportAction.return_from_receiver.value,
        "reviewer": ReportAction.return_from_reviewer.value,
    }
    if payload.from_role not in action_by_role:
        raise HTTPException(status_code=422, detail="from_role must be receiver or reviewer")
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
    # 管理者・管理責任者は承認フロー外のため、受付承認は受付担当のみ
    if not has_role(user, "admin_receiver"):
        raise HTTPException(status_code=403, detail="action not allowed for role")
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_status(reports, ReportStatus.submitted_to_admin.value, ReportStatus.returned_to_receiver.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.receive.value)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.receive.value, reports, user)
    return {"updated": changed}


@router.post("/admin-review-bulk")
async def admin_review_bulk(payload: BulkSubmitIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 管理者・管理責任者は承認フロー外のため、再鑑承認（最終承認）は再鑑者のみ。
    # 旧フローで最終承認待ち(re_reviewed)のまま残った報告書も再鑑者が最終化できる。
    if not has_role(user, "admin_reviewer"):
        raise HTTPException(status_code=403, detail="action not allowed for role")
    reports = _bulk_reports(payload, db, user)
    _validate_bulk_status(reports, ReportStatus.received.value, ReportStatus.re_reviewed.value)
    _validate_target_month(reports, payload.target_month)
    changed = []
    for report in reports:
        transition(db, report, user, ReportAction.re_review.value)
        changed.append(report.id)
    db.commit()
    await send_transition_notifications(db, ReportAction.re_review.value, reports, user)
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


def _validate_bulk_status(reports: list[LessonReport], *allowed_statuses: str) -> None:
    for report in reports:
        if report.status not in allowed_statuses:
            raise HTTPException(status_code=409, detail=f"bulk reports must all be one of {allowed_statuses}")


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


# /admin-approve・/return-from-master エンドポイントは承認フロー変更（再鑑承認＝最終承認、
# 管理者・管理責任者はフロー外）に伴い廃止。
# === Phase 5 END ===
