# === Phase 2: 認証・認可 START ===
from datetime import date, datetime, time
from typing import Literal
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
    roles: list[str] = Field(default_factory=list)
    display_name: str
    tutor_no: str | None = None
    user_no: str | None = None
    phone: str | None = None
    is_active: bool
    skip_parent_approval: bool = False
    created_at: datetime | None = None
    deleted_at: datetime | None = None

    @field_validator("roles", mode="before")
    @classmethod
    def default_roles(cls, roles):
        return roles or []


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int
    total_pages: int
    page: int
    per_page: int
    role_counts: dict[str, int] = Field(default_factory=dict)
    active_admin_master_count: int = 0
    active_admin_chief_count: int = 0


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
    skip_parent_approval: bool | None = None


class UserRolesPatch(BaseModel):
    roles: list[str]

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, roles: list[str]):
        cleaned = []
        for role in roles:
            if role not in cleaned:
                cleaned.append(role)
        return cleaned


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class RegisterInfoOut(BaseModel):
    email: EmailStr
    role: str = "parent"
    role_display: str | None = None
    display_name: str | None = None
    tutor_no: str | None = None
    student_name: str | None = None


class RegisterIn(BaseModel):
    token: str
    display_name: str | None = None
    password: str = Field(min_length=8)


class RegisterOut(BaseModel):
    message: str


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ResetTokenInfoOut(BaseModel):
    valid: bool
    email: EmailStr | None = None
    reason: str | None = None


class AssignmentCreate(BaseModel):
    tutor_id: UUID
    parent_id: UUID | None = None
    parent_email: EmailStr | None = None
    student_name: str


class AssignmentPatch(BaseModel):
    tutor_id: UUID | None = None
    parent_id: UUID | None = None
    student_name: str | None = None
    is_active: bool | None = None
    skip_parent_approval: bool | None = None
    reminder_enabled: bool | None = None
    reminder_days_after: int | None = Field(default=None, ge=1)
    reminder_count: int | None = Field(default=None, ge=1)


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tutor_id: UUID
    parent_id: UUID | None = None
    parent_display_name: str | None = None
    tutor_name: str | None = None
    tutor_no: str | None = None
    parent_name: str | None = None
    parent_no: str | None = None
    parent_email: str | None = None
    student_name: str
    is_active: bool
    skip_parent_approval: bool
    reminder_enabled: bool
    reminder_days_after: int
    reminder_count: int


class StudentOption(BaseModel):
    """担当管理の生徒選択（タイプアヘッド）用の候補。

    既存システムは生徒マスタを持たず、生徒は assignments の (生徒名, 保護者) の一意な組として
    表される。本スキーマはその組を1件の「生徒」候補として返す。保護者No も同梱するため、
    画面側は生徒を選んだだけで保護者（No）を自動表示できる。
    """
    student_name: str
    parent_id: UUID | None = None
    parent_name: str | None = None
    parent_no: str | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "parent"
    display_name: str | None = Field(default=None, max_length=100)
    tutor_no: str | None = Field(default=None, max_length=20)
    assignment_id: UUID | None = None
    student_name: str | None = Field(default=None, max_length=100)
    tutor_id: UUID | None = None


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: EmailStr
    role: str
    assignment_id: UUID | None = None
    tutor_id: UUID | None = None
    tutor_name: str | None = None
    display_name: str | None = None
    tutor_no: str | None = None
    user_no: str | None = None
    student_name: str | None = None
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime
    message: str | None = None


class ReportCreate(BaseModel):
    # 指導報告の内容項目（2026-07 再構築）。「次回の予定/指導日・開始時刻」以外は必須入力だが、
    # 必須の強制は入力UI（フロントの required）で行う。API/スキーマ層は後方互換のため
    # 既存 content（=(b)）のみ必須を維持し、他の内容項目は任意とする（subject は従来も任意）。
    # subject=教科・content=(b)何を指導したか は既存カラム流用。
    assignment_id: UUID
    lesson_date: date
    start_time: time
    end_time: time
    break_minutes: int = Field(default=0, ge=0)
    subject: str | None = Field(default=None, max_length=100)           # 教科
    material_name: str | None = Field(default=None, max_length=2000)    # (a) 使用教材/テキスト名
    content: str = Field(min_length=1, max_length=2000)               # (b) 何を指導したか/単元など
    learning_status: str | None = Field(default=None, max_length=2000)  # (c) 学習状況/問題と対策
    homework_status: Literal["A", "B", "C"] | None = None             # (d) 宿題/状況
    next_homework: str | None = Field(default=None, max_length=2000)    # 次回までの宿題
    next_lesson_date: date | None = None                              # 次回の予定/指導日（任意）
    next_lesson_start: time | None = None                            # 次回の指導開始時刻（任意）

    @field_validator("end_time")
    @classmethod
    def validate_times(cls, end_time, info):
        start_time = info.data.get("start_time")
        if start_time and start_time >= end_time:
            raise ValueError("終了時刻は開始時刻より後の時刻を指定してください")
        return end_time


class ReportPatch(BaseModel):
    # 下書き／差戻し中の部分更新。送信された項目のみ更新（exclude_unset）。
    lesson_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    break_minutes: int | None = Field(default=None, ge=0)
    subject: str | None = Field(default=None, max_length=100)
    material_name: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    learning_status: str | None = Field(default=None, max_length=2000)
    homework_status: Literal["A", "B", "C"] | None = None
    next_homework: str | None = Field(default=None, max_length=2000)
    next_lesson_date: date | None = None
    next_lesson_start: time | None = None


class AdminEditLineIn(BaseModel):
    """受付による月内一括編集の1行（＝1指導日の報告書）。"""
    id: UUID
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
            raise ValueError("終了時刻は開始時刻より後の時刻を指定してください")
        return end_time


class GroupAdminEditIn(BaseModel):
    """受付による報告（生徒×講師×対象月）単位の一括編集リクエスト。
    その月の全指導日を1画面で同時に編集し、まとめて1通の通知を送る。"""
    assignment_id: UUID
    tutor_id: UUID
    target_month: str
    lines: list[AdminEditLineIn]
    comment: str | None = Field(default=None, max_length=2000)


class ReportEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    action: str
    actor_name: str | None = None
    actor_no: str | None = None
    actor_role: str | None = None
    created_at: datetime
    comment: str | None = None


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    assignment_id: UUID
    tutor_id: UUID
    tutor_name: str | None = None
    tutor_no: str | None = None
    parent_id: UUID | None = None
    parent_name: str | None = None
    parent_no: str | None = None
    student_name: str | None = None
    skip_parent_approval: bool = False
    lesson_date: date
    start_time: time
    end_time: time
    break_minutes: int
    subject: str | None                       # 教科
    material_name: str | None = None          # (a) 使用教材/テキスト名
    content: str                              # (b) 何を指導したか/単元など
    learning_status: str | None = None        # (c) 学習状況/問題と対策
    homework_status: str | None = None        # (d) 宿題/状況 A/B/C
    next_homework: str | None = None          # 次回までの宿題
    next_lesson_date: date | None = None      # 次回の予定/指導日
    next_lesson_start: time | None = None     # 次回の指導開始時刻
    status: str
    target_month: str
    submitted_to_parent_at: datetime | None = None
    parent_approved_at: datetime | None = None
    submitted_to_admin_at: datetime | None = None
    received_at: datetime | None = None
    re_reviewed_at: datetime | None = None
    admin_approved_at: datetime | None = None
    stale_since: datetime | None = None
    closed_at: datetime | None = None
    closed_by: UUID | None = None
    closed_by_name: str | None = None
    close_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    last_event: str | None = None
    last_return_comment: str | None = None
    last_return_at: datetime | None = None
    unread_count: int = 0
    events: list[ReportEventOut] = Field(default_factory=list)


class CommentIn(BaseModel):
    comment: str | None = None


class CloseReportRequest(BaseModel):
    close_reason: str


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
