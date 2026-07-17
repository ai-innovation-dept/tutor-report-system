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
    # 就業場所。報告書の「事業所の所在地」の下に表示（契約由来・講師読取専用）
    work_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 教室名。報告書の「事業所の名称」の隣に表示（契約由来・講師読取専用）。
    classroom_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 報告書フォームの項目表示/非表示フラグ（既定は全て表示）。契約からライブ反映する。
    show_dispatch_address: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_work_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_commuter_pass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_break_minutes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_schedule_note: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # コマ設定の使用/未使用（202607170831）。True=使用（従来どおり。設定が無い期は8:40固定ロジック）。
    # False=未使用: コマ設定はグレイアウト（値は保持）し、講師フォームは担当時限列なしの手入力方式
    # （業務開始時間・担当業務・副担当業務・休憩時間を手入力→終了時間のみ自動計算）になる。
    use_period_slots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # コマ設定（担当時限の時間割）。各要素 {"start": "HH:MM", "end": "HH:MM"}（最大10・①から順・重なり不可）。
    # 設定がある契約は講師フォームで選択コマから業務開始・担当業務（分）・休憩時間（分）を自動計算する。
    # 空リストの契約は従来ロジック（開始8:40固定・1コマ50分・休憩(コマ数-1)×10分）。
    period_slots: Mapped[list] = mapped_column(_JSONB, default=list)
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

    def _last_return_event(self):
        # 差戻し要求の許可（approve_return_request）も講師へ差戻る操作のため差戻しとして扱う
        for event in reversed(self.events):
            if event.action in ("return", "approve_return_request"):
                return event
        return None

    @property
    def last_return_comment(self) -> str | None:
        """直近の差戻しコメント（差戻し中の理由表示に使用）。"""
        event = self._last_return_event()
        return event.comment if event else None

    @property
    def last_return_actor_role(self) -> str | None:
        """直近の差戻し元ロール。遷移元から実際に担当した工程を判定する。"""
        event = self._last_return_event()
        if not event:
            return None
        if event.from_status == "awaiting_school":
            return "school"
        if event.from_status in {"awaiting_office_precheck", "awaiting_office", "returned_to_office"}:
            return "office"
        if event.from_status in {"awaiting_sales", "approved"}:
            return "sales"
        return event.actor_role

    def _return_request_state(self):
        """講師起点の差戻し要求の現況を (未解決の要求イベント, 直近の却下イベント) で返す。

        イベント履歴を新しい順に走査し、最初に見つかった要求関連イベントで判定する。
        - request_return → 未解決（承認等でボールが移っても引き継がれる）
        - decline_return_request → 却下済み（講師は再要求できる）
        - 許可・講師へ戻る差戻し・クローズ → 解決済み（どちらも None）
        """
        for event in reversed(self.events):
            if event.action == "request_return":
                return event, None
            if event.action == "decline_return_request":
                return None, event
            if event.action in ("approve_return_request", "close") or event.to_status == "returned_to_tutor":
                return None, None
        return None, None

    @property
    def return_request_pending(self) -> bool:
        """講師の差戻し要求が未解決（ボールを持つロールの許可・却下待ち）か。"""
        pending, _ = self._return_request_state()
        return pending is not None

    @property
    def return_request_comment(self) -> str | None:
        """未解決の差戻し要求の理由（講師が入力したコメント）。未解決の要求が無ければ None。"""
        pending, _ = self._return_request_state()
        return pending.comment if pending else None

    @property
    def return_request_declined_comment(self) -> str | None:
        """直近の差戻し要求が却下された場合、その却下理由。要求中・解決済みは None。"""
        _, declined = self._return_request_state()
        return declined.comment if declined else None

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
        """学校が直近に承認した日時（awaiting_school からの approve イベント）。"""
        for event in reversed(self.events):
            if event.action == "approve" and event.from_status == "awaiting_school":
                return event.created_at
        return None

    @property
    def precheck_approved_at(self) -> datetime | None:
        """事務の事前確認が承認された日時（awaiting_office_precheck からの approve イベント）。

        事前確認フロー（月分超過・1〜9分手入力）では、この日時が講師画面の「学校へ依頼日時」になる。
        """
        for event in reversed(self.events):
            if event.action == "approve" and event.from_status == "awaiting_office_precheck":
                return event.created_at
        return None

    @property
    def submitted_to_school_at(self) -> datetime | None:
        """講師が学校または運営へ提出した日時。"""
        return self.submitted_at

    @property
    def approved_at(self) -> datetime | None:
        """営業が直近に最終承認した日時。"""
        for event in reversed(self.events):
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

    @property
    def school_no(self) -> str | None:
        """紐付け済みの学校の番号（学校番号＝user_no）。未紐付けは None。"""
        if self.assignment and self.assignment.parent:
            return self.assignment.parent.user_no
        return None


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


class WorkMailOutbox(Base):
    """送信待ちメールのキュー（アウトボックス）。

    メールは即時送信せず、まずこのテーブルへ投函(enqueue)する。バックグラウンドの
    ドレイナ(services/mailer.drain_outbox)が「1通ずつ・送信間隔をあけて」順次送信する。
    これにより一括操作・月末ラッシュ等での同時送信／短時間連打を防ぎ、SMTPアカウントの
    スパム判定・ロックを回避する。WorkNotification（アプリ内通知ログ）とは別物で、
    本テーブルは実メール配信の待ち行列のみを担う。
    """
    __tablename__ = "work_mail_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    to_email: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    # pending=未送信 / sent=送信済み / failed=試行上限に達して打ち切り
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
