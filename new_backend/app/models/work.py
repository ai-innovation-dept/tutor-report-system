import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# PostgreSQL: JSONB（インデックス可能）、SQLite: JSON（テスト用）
_JSONB = JSONB().with_variant(JSON(), "sqlite")

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkAssignmentProfile(Base):
    __tablename__ = "work_assignment_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id"), unique=True, index=True)
    form_type: Mapped[str] = mapped_column(String(50))
    contract_meta: Mapped[dict] = mapped_column(_JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    assignment = relationship("Assignment", foreign_keys=[assignment_id])


class WorkReport(Base):
    __tablename__ = "work_reports"
    __table_args__ = (UniqueConstraint("assignment_id", "target_month", name="uq_work_report_assignment_month"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id"), index=True)
    tutor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    target_month: Mapped[str] = mapped_column(String(7), index=True)
    form_type: Mapped[str] = mapped_column(String(50))
    form_data: Mapped[dict] = mapped_column(_JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True)
    current_approver_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    close_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    assignment = relationship("Assignment", foreign_keys=[assignment_id])
    tutor = relationship("User", foreign_keys=[tutor_id])
    closed_by_user = relationship("User", foreign_keys=[closed_by])
    events = relationship(
        "WorkReportEvent",
        primaryjoin="WorkReport.id == WorkReportEvent.report_id",
        order_by="WorkReportEvent.created_at",
        viewonly=True,
    )

    @property
    def last_return_comment(self) -> str | None:
        """直近の差戻しコメント（差戻し中の理由表示に使用）。"""
        for event in reversed(self.events):
            if event.action == "return":
                return event.comment
        return None

    @property
    def student_name(self) -> str | None:
        return self.assignment.student_name if self.assignment else None

    @property
    def tutor_name(self) -> str | None:
        return self.tutor.display_name if self.tutor else None

    @property
    def school_approved_at(self) -> datetime | None:
        """学校が承認した日時（awaiting_school からの approve イベント）。"""
        for event in self.events:
            if event.action == "approve" and event.from_status == "awaiting_school":
                return event.created_at
        return None

    @property
    def submitted_to_school_at(self) -> datetime | None:
        """講師が学校または運営へ提出した日時。"""
        return self.submitted_at

    @property
    def approved_at(self) -> datetime | None:
        """経理が最終承認した日時。"""
        for event in self.events:
            if event.action == "approve" and event.to_status == "approved":
                return event.created_at
        return None

    @property
    def school_name(self) -> str | None:
        """紐付け済みの学校名。未設定なら報告書の派遣先事業所名（meta）を使う。"""
        if self.assignment and self.assignment.parent:
            return self.assignment.parent.display_name
        meta = (self.form_data or {}).get("meta") or {}
        return meta.get("dispatch_place_name") or None


class WorkReportEvent(Base):
    __tablename__ = "work_report_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_reports.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    report = relationship("WorkReport", foreign_keys=[report_id])
    actor = relationship("User", foreign_keys=[actor_id])


class WorkChatMessage(Base):
    __tablename__ = "work_chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_reports.id"), index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    sender = relationship("User", foreign_keys=[sender_id])


class WorkChatRead(Base):
    __tablename__ = "work_chat_reads"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_work_chat_read"),)

    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_chat_messages.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class WorkNotification(Base):
    __tablename__ = "work_notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("work_reports.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="email")
    type: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
