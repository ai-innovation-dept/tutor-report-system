from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import get_active_role, get_current_user
from app.models.shared import Assignment, User
from app.models.work import WorkChatMessage, WorkChatRead, WorkReport
from app.schemas.chat import ChatIn, ChatOut
from app.services.notification_service import record_notification
from app.services.report_service import assert_can_view_report, get_report_or_404

router = APIRouter(prefix="/api/w/reports/{report_id}/messages", tags=["work-chat"])


def _get_report_for_chat(report_id: UUID, user: User, active_role: str, db: Session) -> WorkReport:
    report = get_report_or_404(db, report_id)
    assert_can_view_report(db, report, user, active_role)
    return report


@router.get("", response_model=list[ChatOut])
def list_messages(
    report_id: UUID,
    after_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    _get_report_for_chat(report_id, user, active_role, db)
    stmt = select(WorkChatMessage).where(WorkChatMessage.report_id == report_id).order_by(WorkChatMessage.created_at.asc())
    if after_id:
        after = db.get(WorkChatMessage, after_id)
        if after:
            stmt = stmt.where(WorkChatMessage.created_at > after.created_at)
    return db.scalars(stmt).all()


@router.post("", response_model=ChatOut, status_code=201)
def create_message(
    report_id: UUID,
    payload: ChatIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    report = _get_report_for_chat(report_id, user, active_role, db)
    msg = WorkChatMessage(report_id=report_id, sender_id=user.id, body=payload.body)
    db.add(msg)

    assignment = report.assignment or db.get(Assignment, report.assignment_id)
    for recipient_id in {report.tutor_id, assignment.parent_id if assignment else None} - {user.id, None}:
        recipient = db.get(User, recipient_id)
        if recipient:
            record_notification(db, recipient, report, "chat_message", "New report message", payload.body)

    db.commit()
    db.refresh(msg)
    return msg


@router.post("/{msg_id}/read")
def mark_read(
    report_id: UUID,
    msg_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    _get_report_for_chat(report_id, user, active_role, db)
    msg = db.get(WorkChatMessage, msg_id)
    if not msg or msg.report_id != report_id:
        raise HTTPException(status_code=404, detail="message not found")
    if not db.get(WorkChatRead, {"message_id": msg_id, "user_id": user.id}):
        db.add(WorkChatRead(message_id=msg_id, user_id=user.id))
        db.commit()
    return {"status": "ok"}
