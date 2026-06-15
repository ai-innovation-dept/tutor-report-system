import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

# PostgreSQL: JSONB（インデックス可能）、SQLite: JSON（テスト用）
_JSONB = JSONB().with_variant(JSON(), "sqlite")

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkAssignmentProfile(Base):
    """講師×学校の契約情報（兼 assignment のフォーム設定）。

    1契約 = 1 assignment（(tutor, school) ごと）に対応する。第2弾で報告書フォームへ
    自動反映する契約マスタを兼ねる。
    """
    __tablename__ = "work_assignment_profiles"
    __table_args__ = (
        UniqueConstraint("tutor_id", "school_id", name="uq_work_profile_tutor_school"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id"), unique=True, index=True)
    form_type: Mapped[str] = mapped_column(String(50))
    contract_meta: Mapped[dict] = mapped_column(_JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # --- 契約情報（第1弾で追加） ---
    tutor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    school_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    customer_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    our_staff: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 派遣先事業所の所在地。報告書の同名欄へ自動反映（講師側は読取専用）
    dispatch_place_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    contract_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    monthly_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weekly_lessons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 月時間（分）・週コマの期間付き複数ケース。
    # 各要素: {"monthly_minutes": int|None, "weekly_lessons": int|None,
    #          "start_date": "YYYY-MM-DD"|None, "end_date": "YYYY-MM-DD"|None}
    # 旧 monthly_minutes / weekly_lessons は CSV取込の入力互換用に残す（表示はこちらが正）。
    workload_cases: Mapped[list] = mapped_column(_JSONB, default=list)
    shift_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 委託業務はメイン業務（①〜③・①必須）とサブ業務（①〜⑤・任意）の2区分。
    # 報告書の列はメイン→サブの順に生成される（task_minutes_N / sub_minutes_N）。
    task_name_1: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_name_2: Mapped[str | None] = mapped_column(String(100), nullable=True)
    task_name_3: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_task_name_1: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_task_name_2: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_task_name_3: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_task_name_4: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_task_name_5: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 採点（専用欄）: 有効時のみ報告書に「{項目名}（{単位}）」列（1セル併記＝回数＋分数固定）を生成。
    # 項目名・単位は任意入力（既定: 採点／回）。分は常に固定。
    scoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scoring_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scoring_unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    scoring_task_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scoring_contract_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    task_id_1: Mapped[str | None] = mapped_column(String(50), nullable=True)
    task_id_2: Mapped[str | None] = mapped_column(String(50), nullable=True)
    task_id_3: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contract_id_1: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contract_id_2: Mapped[str | None] = mapped_column(String(50), nullable=True)
    contract_id_3: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_task_id_1: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_task_id_2: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_task_id_3: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_task_id_4: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_task_id_5: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_contract_id_1: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_contract_id_2: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_contract_id_3: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_contract_id_4: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_contract_id_5: Mapped[str | None] = mapped_column(String(50), nullable=True)

    assignment = relationship("Assignment", foreign_keys=[assignment_id])
    tutor = relationship("User", foreign_keys=[tutor_id])
    school = relationship("User", foreign_keys=[school_id])


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
    def tutor_no(self) -> str | None:
        """提出した講師の番号（講師番号）。表示用に tutor_no→user_no の順で解決する。"""
        if not self.tutor:
            return None
        return self.tutor.tutor_no or self.tutor.user_no

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

    @property
    def actor_name(self) -> str | None:
        return self.actor.display_name if self.actor else None

    @property
    def actor_role(self) -> str | None:
        return self.actor.role if self.actor else None


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
