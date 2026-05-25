# === Phase 6: アプリ内チャット START ===
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_report_for_user
from app.models import ChatMessage, ChatRead, User
from app.schemas import ChatIn, ChatOut
from app.services.notification_service import enqueue

router = APIRouter(prefix="/api/reports/{report_id}/messages", tags=["chat"])


@router.get("", response_model=list[ChatOut])
def list_messages(report_id: UUID, after_id: UUID | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_report_for_user(report_id, user, db)
    stmt = select(ChatMessage).where(ChatMessage.report_id == report_id).order_by(ChatMessage.created_at.asc())
    if after_id:
        after = db.get(ChatMessage, after_id)
        if after:
            stmt = stmt.where(ChatMessage.created_at > after.created_at)
    return db.scalars(stmt).all()


@router.post("", response_model=ChatOut)
def create_message(report_id: UUID, payload: ChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = get_report_for_user(report_id, user, db)
    msg = ChatMessage(report_id=report_id, sender_id=user.id, body=payload.body)
    db.add(msg)
    for recipient in {report.tutor_id, report.parent_id} - {user.id}:
        enqueue(db, recipient, "chat_message", "New report message", payload.body, report_id)
    db.commit()
    db.refresh(msg)
    return msg


@router.post("/{msg_id}/read")
def mark_read(report_id: UUID, msg_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_report_for_user(report_id, user, db)
    msg = db.get(ChatMessage, msg_id)
    if not msg or msg.report_id != report_id:
        raise HTTPException(status_code=404, detail="message not found")
    if not db.get(ChatRead, {"message_id": msg_id, "user_id": user.id}):
        db.add(ChatRead(message_id=msg_id, user_id=user.id))
        db.commit()
    return {"status": "ok"}
# === Phase 6 END ===

