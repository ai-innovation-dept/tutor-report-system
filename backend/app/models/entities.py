# === Phase 1: データベース層 START ===
import enum
import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    tutor = "tutor"
    parent = "parent"
    admin_receiver = "admin_receiver"
    admin_reviewer = "admin_reviewer"
    admin_master = "admin_master"


class ReportStatus(str, enum.Enum):
    draft = "draft"
    awaiting_parent_approval = "awaiting_parent_approval"
    parent_approved = "parent_approved"
    submitted_to_admin = "submitted_to_admin"
    received = "received"
    re_reviewed = "re_reviewed"
    admin_approved = "admin_approved"
    returned_to_tutor = "returned_to_tutor"
    returned_to_receiver = "returned_to_receiver"
    closed = "closed"


class ReportAction(str, enum.Enum):
    create = "create"
    update = "update"
    submit_to_parent = "submit_to_parent"
    parent_approve = "parent_approve"
    parent_return = "parent_return"
    submit_to_admin = "submit_to_admin"
    receive = "receive"
    return_from_receiver = "return_from_receiver"
    re_review = "re_review"
    return_from_reviewer = "return_from_reviewer"
    admin_approve = "admin_approve"
    return_from_master = "return_from_master"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), index=True)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    display_name: Mapped[str] = mapped_column(String(100))
    tutor_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    skip_parent_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Assignment(Base):
    __tablename__ = "assignments"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tutor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    student_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 'legacy' = 指導実績報告システム、'new' = 業務連絡表システム。両システムで assignments テーブルを共有するため、
    # この識別子で自システムのレコードのみを絞り込む。物理カラムは new_backend のマイグレーションで既に存在。
    system_type: Mapped[str] = mapped_column(String(10), default="legacy")
    skip_parent_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_days_after: Mapped[int] = mapped_column(Integer, default=1)
    reminder_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    tutor: Mapped[User] = relationship(foreign_keys=[tutor_id])
    parent: Mapped[User | None] = relationship(foreign_keys=[parent_id])

    @property
    def parent_display_name(self) -> str | None:
        return self.parent.display_name if self.parent else None

    @property
    def tutor_name(self) -> str | None:
        return self.tutor.display_name if self.tutor else None

    @property
    def parent_name(self) -> str | None:
        return self.parent.display_name if self.parent else None

    @property
    def parent_email(self) -> str | None:
        return self.parent.email if self.parent else None


class Invitation(Base):
    __tablename__ = "invitations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(32), default=UserRole.parent.value)
    display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tutor_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assignments.id"), nullable=True, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    assignment: Mapped[Assignment | None] = relationship()
    inviter: Mapped[User | None] = relationship(foreign_keys=[invited_by])


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[User] = relationship()


class LessonReport(Base):
    __tablename__ = "lesson_reports"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id"), index=True)
    tutor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    lesson_date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    break_minutes: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True, default=ReportStatus.draft.value)
    target_month: Mapped[str] = mapped_column(String(7), index=True)
    submitted_to_parent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parent_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_to_admin_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    re_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stale_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    close_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    assignment: Mapped[Assignment] = relationship()
    tutor: Mapped[User] = relationship(foreign_keys=[tutor_id])
    parent: Mapped[User | None] = relationship(foreign_keys=[parent_id])
    closed_by_user: Mapped[User | None] = relationship(foreign_keys=[closed_by])

    @property
    def skip_parent_approval(self) -> bool:
        return bool(self.parent and self.parent.skip_parent_approval)


class ReportEvent(Base):
    __tablename__ = "report_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lesson_reports.id"), index=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report: Mapped[LessonReport] = relationship()
    actor: Mapped[User] = relationship()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lesson_reports.id"), index=True)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sender: Mapped[User] = relationship()


class ChatRead(Base):
    __tablename__ = "chat_reads"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_chat_read"),)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chat_messages.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lesson_reports.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(16), default="email")
    type: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
# === Phase 1 END ===
