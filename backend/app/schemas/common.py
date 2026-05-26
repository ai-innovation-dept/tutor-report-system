# === Phase 2: 認証・認可 START ===
from datetime import date, datetime, time
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    role: str
    display_name: str
    tutor_no: str | None = None
    phone: str | None = None
    is_active: bool


class UserCreate(BaseModel):
    email: EmailStr
    role: str
    display_name: str
    tutor_no: str | None = None
    phone: str | None = None
    password: str | None = None


class UserPatch(BaseModel):
    display_name: str | None = None
    tutor_no: str | None = None
    phone: str | None = None
    is_active: bool | None = None
    role: str | None = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RegisterInfoOut(BaseModel):
    email: EmailStr
    student_name: str | None = None


class RegisterIn(BaseModel):
    token: str
    password: str = Field(min_length=8)


class AssignmentCreate(BaseModel):
    tutor_id: UUID
    parent_id: UUID | None = None
    student_name: str


class AssignmentPatch(BaseModel):
    tutor_id: UUID | None = None
    parent_id: UUID | None = None
    student_name: str | None = None
    is_active: bool | None = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tutor_id: UUID
    parent_id: UUID | None = None
    parent_display_name: str | None = None
    student_name: str
    is_active: bool


class InvitationCreate(BaseModel):
    email: EmailStr
    tutor_id: UUID
    student_name: str = Field(min_length=1, max_length=100)


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    role: str
    assignment_id: UUID | None = None
    tutor_id: UUID | None = None
    tutor_name: str | None = None
    student_name: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime
    message: str | None = None


class ReportCreate(BaseModel):
    assignment_id: UUID
    lesson_date: date
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0)
    subject: str | None = None
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("end_time")
    @classmethod
    def validate_times(cls, end_time, info):
        start_time = info.data.get("start_time")
        if start_time and start_time >= end_time:
            raise ValueError("start_time must be before end_time")
        return end_time


class ReportPatch(BaseModel):
    lesson_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(default=None, ge=0)
    subject: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=2000)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    assignment_id: UUID
    tutor_id: UUID
    tutor_name: str | None = None
    parent_id: UUID | None = None
    student_name: str | None = None
    lesson_date: date
    start_time: time
    end_time: time
    break_minutes: int
    subject: str | None
    content: str
    status: str
    target_month: str
    submitted_to_parent_at: datetime | None = None
    parent_approved_at: datetime | None = None
    submitted_to_admin_at: datetime | None = None
    received_at: datetime | None = None
    re_reviewed_at: datetime | None = None
    admin_approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_event: str | None = None
    last_return_comment: str | None = None
    last_return_at: datetime | None = None
    unread_count: int = 0


class CommentIn(BaseModel):
    comment: str | None = None


class BulkSubmitIn(BaseModel):
    report_ids: list[UUID]
    target_month: str | None = None


class BulkReturnIn(BaseModel):
    report_ids: list[UUID]
    comment: str = Field(min_length=1)
    target_month: str | None = None

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, comment: str):
        if not comment.strip():
            raise ValueError("comment is required")
        return comment.strip()


class AdminBulkReturnIn(BulkReturnIn):
    from_role: str

    @field_validator("from_role")
    @classmethod
    def validate_from_role(cls, from_role: str):
        if from_role not in {"receiver", "reviewer", "master"}:
            raise ValueError("from_role must be receiver, reviewer, or master")
        return from_role


class ChatIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    report_id: UUID
    sender_id: UUID
    body: str
    created_at: datetime
# === Phase 6 END ===
