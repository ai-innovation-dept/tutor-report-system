# === 提出締切通知（改修依頼 202607161428） START ===
"""指導報告の提出締切（翌月第一営業日）の計算と、講師向け締切通知。

締切ルール:
- 対象月Mの提出締切 = Mの翌月の「第一営業日」。
- 営業日 = 土日・日本の祝日(jpholiday)・BUSINESS_CLOSED_DAYS(年末年始等の休業日)を除く日。
- 通知は2段階: 1回目=月中（既定15日）/ 2回目=締切前日（前日〜締切当日は「至急」扱い）。

画面バナー（①）は active_notice() を base.html（講師ロールのみ）から参照する。
日付だけで決まるためDB不要・レンダー時に都度評価する。

メール（②）は日次ジョブ(run_deadline_notice_job)が due_email_notices() の送信窓に
入った日に「月×種別につき1回だけ」送る（deadline_notice_sends が送信済みガード。
停止日を挟んでも窓期間内の次回起動で追い送りされ、再送はガードで防がれる）。
誤送信防止のため DEADLINE_NOTICE_ENABLED=true のときのみ送信する（既定は無効）。
"""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from logging import getLogger

import jpholiday
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.core.time import JST, get_current_jst_date
from app.database import SessionLocal
from app.models import Assignment, DeadlineNoticeSend, LessonReport, MailOutbox, ReportStatus, User
from app.services.notification_service import _render_email_template, enqueue

logger = getLogger(__name__)

DEADLINE_FIRST_TYPE = "deadline_first"
DEADLINE_EVE_TYPE = "deadline_eve"

_SUBJECTS = {
    DEADLINE_FIRST_TYPE: "【重要】指導報告提出締切のお知らせ",
    DEADLINE_EVE_TYPE: "【至急確認依頼】指導報告提出締切のお知らせ",
}
_TEMPLATES = {
    DEADLINE_FIRST_TYPE: "deadline_first.txt",
    DEADLINE_EVE_TYPE: "deadline_eve.txt",
}

# 講師の手元に残っている（＝まだ提出が完了していない）ステータス
_UNSUBMITTED_STATUSES = {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value}

_WEEKDAY_LABELS = "月火水木金土日"


def _closed_month_days() -> set[str]:
    """毎年休業扱いにする日（MM-DD）の集合。既定は年末年始休業。"""
    return {token.strip() for token in (settings.business_closed_days or "").split(",") if token.strip()}


def is_business_day(day: date) -> bool:
    if day.weekday() >= 5:  # 土日
        return False
    if jpholiday.is_holiday(day):  # 日本の祝日（振替休日含む）
        return False
    return day.strftime("%m-%d") not in _closed_month_days()


def first_business_day(year: int, month: int) -> date:
    day = date(year, month, 1)
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def submission_deadline(target_month: str) -> date:
    """対象月(YYYY-MM)の提出締切 = 翌月の第一営業日。"""
    year, month = (int(part) for part in target_month.split("-"))
    if month == 12:
        year, month = year + 1, 1
    else:
        month += 1
    return first_business_day(year, month)


def _midmonth_start(month_first: date) -> date:
    """1回目（月中）通知の開始日。設定日が月の日数を超える場合は月末に丸める。"""
    last_day = calendar.monthrange(month_first.year, month_first.month)[1]
    return month_first.replace(day=min(max(1, settings.deadline_notice_midmonth_day), last_day))


def _deadline_label(deadline: date) -> str:
    return f"{deadline.month}月{deadline.day}日（{_WEEKDAY_LABELS[deadline.weekday()]}）"


def _candidate_months(today: date) -> list[date]:
    """通知対象になり得る月（月初日）。前月分（締切が今月頭に食い込む）を優先して判定する。"""
    this_first = today.replace(day=1)
    prev_first = (this_first - timedelta(days=1)).replace(day=1)
    return [prev_first, this_first]


def active_notice(today: date | None = None) -> dict | None:
    """本日時点で画面に表示すべき締切通知。表示期間外は None。

    表示期間: 対象月の月中通知日（既定15日）〜締切当日。締切前日からは「至急」フェーズ。
    既定設定では高々1つの対象月にしか該当しない（前月分の締切は遅くとも今月上旬に終わる）。
    """
    today = today or get_current_jst_date()
    for month_first in _candidate_months(today):
        target_month = month_first.strftime("%Y-%m")
        deadline = submission_deadline(target_month)
        if not (_midmonth_start(month_first) <= today <= deadline):
            continue
        phase = "urgent" if today >= deadline - timedelta(days=1) else "info"
        return {
            "phase": phase,
            "target_month": target_month,
            "month_label": f"{month_first.month}月",
            "deadline": deadline,
            "deadline_label": _deadline_label(deadline),
        }
    return None


def due_email_notices(today: date | None = None) -> list[tuple[str, str]]:
    """本日メール送信すべき (通知種別, 対象月YYYY-MM) の一覧。

    送信窓: 1回目=[月中通知日, 締切2日前] / 2回目=[締切前日, 締切当日]。
    窓方式のためジョブが停止日を挟んでも次回起動で追い送りされる（再送は送信済みガードで防ぐ）。
    """
    today = today or get_current_jst_date()
    due: list[tuple[str, str]] = []
    for month_first in _candidate_months(today):
        target_month = month_first.strftime("%Y-%m")
        deadline = submission_deadline(target_month)
        if _midmonth_start(month_first) <= today <= deadline - timedelta(days=2):
            due.append((DEADLINE_FIRST_TYPE, target_month))
        elif deadline - timedelta(days=1) <= today <= deadline:
            due.append((DEADLINE_EVE_TYPE, target_month))
    return due


def unsubmitted_tutors(db: Session, target_month: str) -> list[User]:
    """対象月の提出が完了していない講師（重複なし）。

    有効な既存システム(legacy)担当を持つ有効な講師のうち、いずれかの担当で
    「対象月の報告書行が未作成」または「draft/returned_to_tutor の行が残っている」講師。
    全行が承認フロー上（awaiting〜admin_approved）か closed のみの担当は提出済み扱い。
    対象月終了後に作成された担当は対象外（その月の指導関係がないため）。
    """
    assignments = db.scalars(
        select(Assignment)
        .join(User, Assignment.tutor_id == User.id)
        .where(
            Assignment.is_active.is_(True),
            Assignment.system_type == "legacy",
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .options(selectinload(Assignment.tutor))
    ).all()
    if not assignments:
        return []

    statuses_by_assignment: dict = defaultdict(set)
    rows = db.execute(
        select(LessonReport.assignment_id, LessonReport.status).where(LessonReport.target_month == target_month)
    ).all()
    for assignment_id, status in rows:
        statuses_by_assignment[assignment_id].add(status)

    year, month = (int(part) for part in target_month.split("-"))
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    # 対象月の翌月初(JST)より後に作られた担当は「対象月に指導関係がない」ため対象外
    month_end_cutoff = datetime(next_year, next_month, 1, tzinfo=JST).astimezone(timezone.utc)

    tutors: dict = {}
    for assignment in assignments:
        created_at = assignment.created_at
        if created_at is not None:
            if created_at.tzinfo is None:  # SQLite(テスト)はnaive UTCで返る
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at >= month_end_cutoff:
                continue
        statuses = statuses_by_assignment.get(assignment.id, set())
        if statuses and not (statuses & _UNSUBMITTED_STATUSES):
            continue  # 全行が提出済み（承認フロー上/closed）の担当
        if assignment.tutor:
            tutors.setdefault(assignment.tutor_id, assignment.tutor)
    return list(tutors.values())


def enqueue_deadline_notices(db: Session, today: date | None = None) -> int:
    """本日送るべき締切通知メールを未提出講師へ投函する。投函した通数を返す（コミットは呼び出し側）。

    Notification（アプリ内通知ログ）・MailOutbox（実配信キュー）・DeadlineNoticeSend（送信済みガード）
    を同一トランザクションで書くことで、二重送信・取りこぼしを構造的に防ぐ。
    """
    if not settings.deadline_notice_enabled:
        return 0
    today = today or get_current_jst_date()
    queued = 0
    for notice_type, target_month in due_email_notices(today):
        already_sent = db.scalar(
            select(func.count(DeadlineNoticeSend.id)).where(
                DeadlineNoticeSend.target_month == target_month,
                DeadlineNoticeSend.notice_type == notice_type,
            )
        )
        if already_sent:
            continue
        deadline = submission_deadline(target_month)
        subject = _SUBJECTS[notice_type]
        body = _render_email_template(
            _TEMPLATES[notice_type],
            {
                "month_label": f"{int(target_month[5:7])}月",
                "deadline_label": _deadline_label(deadline),
                "base_url": settings.base_url.rstrip("/"),
            },
        )
        recipients = 0
        for tutor in unsubmitted_tutors(db, target_month):
            if not tutor.email:
                continue
            notification = enqueue(db, tutor.id, notice_type, subject, body)
            notification.sent_at = datetime.now(timezone.utc)  # 投函時刻（実配信はドレイナが行う）
            db.add(MailOutbox(to_email=tutor.email, subject=subject, body=body, status="pending"))
            recipients += 1
        db.add(DeadlineNoticeSend(target_month=target_month, notice_type=notice_type, recipient_count=recipients))
        db.flush()
        queued += recipients
        logger.info("deadline notice queued: type=%s month=%s recipients=%s", notice_type, target_month, recipients)
    return queued


def run_deadline_notice_job() -> None:
    """日次（09:00 JST）。提出締切通知メールの投函を行う（他リマインドとはセッション・コミットを分離）。"""
    db = SessionLocal()
    try:
        enqueue_deadline_notices(db)
        db.commit()
    finally:
        db.close()
# === 提出締切通知（改修依頼 202607161428） END ===
