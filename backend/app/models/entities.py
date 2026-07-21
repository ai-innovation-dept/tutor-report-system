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
    admin_chief = "admin_chief"


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
    receiver_edit = "receiver_edit"  # 受付担当による報告書修正
    # 講師起点の差戻し要求（改修依頼 202607211144）。要求・却下はステータスを変えずイベントのみ記録し、
    # 許可で講師へ差戻す。未解決かどうかは report_events から導出する（DBカラムは追加しない）。
    request_return = "request_return"                    # 講師 → 現在のボール保持者へ差戻しを要求
    approve_return_request = "approve_return_request"    # ボール保持者が許可（＝講師へ差戻し）
    decline_return_request = "decline_return_request"    # ボール保持者が却下（理由必須）


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), index=True)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    display_name: Mapped[str] = mapped_column(String(100))
    tutor_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 全ロール共通のユーザーNo（T1xxx=講師 / T2xxx=保護者 / T3xxx=運営スタッフ）。
    # 物理カラムは new_backend のマイグレーション 0002 で追加済み。採番は user_no_service が管理。
    user_no: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    skip_parent_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    # アクセス可能システムの配列（'legacy'=指導実績報告システム / 'new'=業務連絡表システム）。
    # 物理カラムは new_backend のマイグレーション 0002 で追加済み。所属判定の唯一の基準。
    allowed_systems: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # 初回ログイン時のパスワード変更を必須にするフラグ。新システムのCSV一括作成ユーザー向け。
    # 物理カラムはマイグレーション0015で追加。既存システムでは未使用（読み取りモデルの整合のため定義）。
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
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
    def tutor_no(self) -> str | None:
        return self.tutor.user_no if self.tutor else None

    @property
    def parent_name(self) -> str | None:
        return self.parent.display_name if self.parent else None

    @property
    def parent_no(self) -> str | None:
        return self.parent.user_no if self.parent else None

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
    # 指導報告の内容項目（2026-07 再構築）。subject=「教科」・content=「(b) 何を指導したか/単元など」として流用。
    # 学年は「教科」の前に置く先頭項目（区分＝小/中/高＋学年数）。必須の強制は入力UI側で行う。
    grade_level: Mapped[str | None] = mapped_column(String(2), nullable=True)  # 学年区分 小/中/高
    grade_year: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 学年数（小1〜6・中/高1〜3）
    subject: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 教科
    content: Mapped[str] = mapped_column(Text)  # (b) 何を指導したか/単元など
    material_name: Mapped[str | None] = mapped_column(Text, nullable=True)  # (a) 使用教材/テキスト名
    learning_status: Mapped[str | None] = mapped_column(Text, nullable=True)  # (c) 学習状況/問題と対策
    homework_status: Mapped[str | None] = mapped_column(String(1), nullable=True)  # (d) 宿題/状況 A/B/C
    next_homework: Mapped[str | None] = mapped_column(Text, nullable=True)  # 次回までの宿題
    next_lesson_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # 次回の予定/指導日
    next_lesson_start: Mapped[time | None] = mapped_column(Time, nullable=True)  # 次回の指導開始時刻
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


class MonthlyReport(Base):
    """指導月報（原本: docs/イスト勤怠レポート for 代々木進学会/原本_月報.pdf）。

    担当（assignment）×対象月で1件。講師が承認依頼前に作成・更新し、
    保護者は承認時に保護者記入欄（parent_note）を記入する（講師は記入不可）。
    学年以外のフォーム内容（問題点と対策・志望校・テスト結果・指導実施日・
    今月を振り返って・連絡事項）は form_data(JSON) に保持する。
    """
    __tablename__ = "monthly_reports"
    __table_args__ = (UniqueConstraint("assignment_id", "target_month", name="uq_monthly_report_assignment_month"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assignments.id"), index=True)
    tutor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    target_month: Mapped[str] = mapped_column(String(7), index=True)
    grade: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 学年（フリーフォーマット・必須はUI＋承認依頼ガードで強制）
    form_data: Mapped[dict] = mapped_column(JSON, default=dict)
    # 保護者記入欄（ご要望/連絡事項）。保護者が承認時に記入する（講師は記入不可）
    parent_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_note_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    parent_note_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    assignment: Mapped[Assignment] = relationship()
    tutor: Mapped[User] = relationship(foreign_keys=[tutor_id])
    parent: Mapped[User | None] = relationship(foreign_keys=[parent_id])


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


class DeadlineNoticeSend(Base):
    """提出締切メール通知（deadline_first=月中 / deadline_eve=締切前日）の送信済み記録。

    「月×種別につき1回だけ送る」ためのガード。日次ジョブが停止日を挟んでも、送信窓の
    期間内の次回起動で未送信分を追い送りでき、再送はこの記録で防ぐ。宛先ごとの内容は
    notifications（アプリ内通知ログ）と mail_outbox（実配信キュー）に残る。
    """
    __tablename__ = "deadline_notice_sends"
    __table_args__ = (UniqueConstraint("target_month", "notice_type", name="uq_deadline_notice_month_type"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_month: Mapped[str] = mapped_column(String(7), index=True)
    notice_type: Mapped[str] = mapped_column(String(32))
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MailOutbox(Base):
    """送信待ちメールのキュー（アウトボックス）。

    メールは即時送信せず、まずこのテーブルへ投函(enqueue)する。バックグラウンドの
    ドレイナ(services/mailer.drain_outbox)が「1通ずつ・送信間隔をあけて」順次送信する。
    これにより一括操作・月末ラッシュ等での同時送信／短時間連打を防ぎ、SMTPアカウントの
    スパム判定・ロックを回避する。Notification（アプリ内通知ログ）とは別物で、本テーブルは
    実メール配信の待ち行列のみを担う。新システム(new_backend)の work_mail_outbox と対。
    """
    __tablename__ = "mail_outbox"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    to_email: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    # pending=未送信 / sent=送信済み / failed=試行上限に達して打ち切り
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
# === Phase 1 END ===
