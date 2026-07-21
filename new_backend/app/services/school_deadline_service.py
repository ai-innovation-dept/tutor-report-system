"""学校ごとの締め日（提出期限）設定と、締め日前の【至急確認】メール（改修依頼 202607161140）。

- 設定はユーザ管理（学校ユーザーの詳細ドロワー）で行う:
  早期チェックON/OFF・通知日数（締め日の何日前に送るか）・月ごとの締め日（年間分を月単位で設定）。
- 早期チェックがONの学校のみ、「締め日の notice_days_before 日前 〜 締め日当日」の窓に入った
  最初の日次ジョブ（09:00 JST）で1回だけ、営業（sales ロールの有効ユーザー全員）へ
  「締め日は〇〇です、提出状況を確認してください」メールを【至急確認】タイトルで送る。
  窓方式のためジョブ停止日を挟んでも締め日までは追い送りされる（締め日を過ぎた月は送らない）。
- 送信済みガードは work_school_deadlines.notice_sent_at（月×学校につき1回）。
  締め日を変更するとガードが解除され、新しい締め日の窓で再送対象になる。
- 契約講師全員の学校承認が完了している学校には送らない（完了メールで通知済みのため。
  窓内に差戻し等で未完了へ戻った場合は翌日以降のジョブで送る）。
"""
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.shared import User
from app.models.work import WorkSchoolDeadline, WorkSchoolSetting
from app.services.notification_service import _staff_users, enqueue_email_template
from app.services.school_progress_service import (
    SchoolMonthProgress,
    _record_school_notification,
    _school_label,
    _tutor_label,
    school_month_progress,
)

logger = logging.getLogger(__name__)

_NOTICE_TYPE = "school_deadline_notice"
_WEEKDAYS_JA = "月火水木金土日"

DEFAULT_NOTICE_DAYS_BEFORE = 3


def deadline_within_month(target_month: str, value: date) -> bool:
    """締め日が対象月内の日付かどうか（202607161332: N月分はN月の日付のみ設定可）。"""
    return value.strftime("%Y-%m") == target_month


def _month_label(target_month: str) -> str:
    year, month = target_month.split("-")
    return f"{year}年{int(month)}月"


def _current_jst_date() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


def _deadline_label(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日（{_WEEKDAYS_JA[value.weekday()]}）"


def _notice_subject(school: User, target_month: str) -> str:
    # 早期チェックONの学校向けのため、タイトルには必ず【至急確認】を付ける（202607161140）
    return f"【至急確認】締め日前の提出状況のご確認（{target_month}分 {school.display_name}）"


# ---------------------------------------------------------------------------
# 設定の読み書き（ユーザ管理の学校設定APIから使用）
# ---------------------------------------------------------------------------

def get_school_setting(db: Session, school_id) -> WorkSchoolSetting | None:
    return db.scalar(select(WorkSchoolSetting).where(WorkSchoolSetting.school_id == school_id))


def deadlines_for_year(db: Session, school_id, year: int) -> list[WorkSchoolDeadline]:
    """指定年（YYYY-01〜YYYY-12）の締め日設定を対象月順に返す。"""
    prefix = f"{year:04d}-"
    return list(
        db.scalars(
            select(WorkSchoolDeadline)
            .where(
                WorkSchoolDeadline.school_id == school_id,
                WorkSchoolDeadline.target_month.like(f"{prefix}%"),
            )
            .order_by(WorkSchoolDeadline.target_month)
        )
    )


def copy_school_settings(db: Session, source_school_id, target_school_id) -> int:
    """学校の締め日通知設定を別の学校ユーザーへ複製する（改修依頼 202607210807 ①）。

    複製するのは早期チェックON/OFF・通知日数と、登録済みの締め日（年を問わず全件）。
    送信済みガード（notice_sent_at）は引き継がず未送信として作る（コピー先は新しい学校で、
    これから確認メールの対象になるため）。戻り値は複製した締め日の件数。
    コピー先に既存の設定がある場合は上書きしない前提（新規作成直後の呼び出しのみを想定）。
    """
    source_setting = get_school_setting(db, source_school_id)
    if source_setting is not None:
        db.add(
            WorkSchoolSetting(
                school_id=target_school_id,
                early_check_enabled=source_setting.early_check_enabled,
                notice_days_before=source_setting.notice_days_before,
            )
        )
    rows = db.scalars(
        select(WorkSchoolDeadline).where(WorkSchoolDeadline.school_id == source_school_id)
    ).all()
    for row in rows:
        db.add(
            WorkSchoolDeadline(
                school_id=target_school_id,
                target_month=row.target_month,
                deadline_date=row.deadline_date,
            )
        )
    db.flush()
    return len(rows)


def save_school_settings(
    db: Session,
    school: User,
    *,
    early_check_enabled: bool,
    notice_days_before: int,
    deadlines: dict[str, date | None],
) -> None:
    """学校の締め日通知設定を保存する。deadlines は {対象月: 締め日 or None(削除)}。

    渡された対象月のみ更新・削除する（他の年・月の設定には触れない）。
    締め日を変更した月は notice_sent_at を解除し、新しい締め日の窓で再送対象に戻す。
    締め日は対象月内の日付のみ（202607161332）。範囲外は ValueError（API側で422に変換）。
    """
    for month, value in deadlines.items():
        if value is not None and not deadline_within_month(month, value):
            raise ValueError(
                f"{_month_label(month)}分の締め日は{_month_label(month)}内の日付で指定してください（指定値: {value}）"
            )
    setting = get_school_setting(db, school.id)
    if setting is None:
        setting = WorkSchoolSetting(school_id=school.id)
        db.add(setting)
    setting.early_check_enabled = early_check_enabled
    setting.notice_days_before = notice_days_before

    months = list(deadlines.keys())
    existing = {}
    if months:
        existing = {
            row.target_month: row
            for row in db.scalars(
                select(WorkSchoolDeadline).where(
                    WorkSchoolDeadline.school_id == school.id,
                    WorkSchoolDeadline.target_month.in_(months),
                )
            )
        }
    for month, value in deadlines.items():
        row = existing.get(month)
        if value is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            db.add(WorkSchoolDeadline(school_id=school.id, target_month=month, deadline_date=value))
        elif row.deadline_date != value:
            row.deadline_date = value
            row.notice_sent_at = None  # 締め日変更＝確認メールを再送対象に戻す
    db.flush()


# ---------------------------------------------------------------------------
# 締め日前の確認メール（日次ジョブ）
# ---------------------------------------------------------------------------

def _progress_block(progress: SchoolMonthProgress | None) -> str:
    """メール本文に載せる現在の学校承認状況（対象外の学校＝スキップ校等は空文字）。"""
    if not progress:
        return ""
    lines = [f"学校承認の状況：承認済み {len(progress.approved_entries)}/{len(progress.entries)}名"]
    if progress.pending_entries:
        lines.append("【未承認】")
        lines.extend(f"・{_tutor_label(e.tutor)}：{e.label}" for e in progress.pending_entries)
    if progress.no_lesson_entries:
        lines.append(f"（このほか当月授業なし申請 {len(progress.no_lesson_entries)}名は対象外）")
    return "\n" + "\n".join(lines) + "\n"


def _enqueue_deadline_notice(db: Session, school: User, deadline: WorkSchoolDeadline, progress) -> None:
    subject = _notice_subject(school, deadline.target_month)
    context = {
        "school_label": _school_label(school),
        "target_month": deadline.target_month,
        "deadline_label": _deadline_label(deadline.deadline_date),
        "progress_block": _progress_block(progress),
        "base_url": settings.NEW_BASE_URL.rstrip("/"),
    }
    body_log = (
        f"{school.display_name}の{deadline.target_month}分の締め日は"
        f"{_deadline_label(deadline.deadline_date)}です。提出状況を確認してください。"
    )
    for sales in _staff_users(db, "sales"):
        enqueue_email_template(db, sales.email, subject, "notify_school_deadline_notice.txt", context)
        _record_school_notification(db, sales, _NOTICE_TYPE, subject, body_log)


def enqueue_school_deadline_notices(db: Session, today: date | None = None) -> int:
    """早期チェックONの学校の締め日前確認メールを営業全員へ投函する。戻り値は送信した学校×月の数。

    「締め日−通知日数 〜 締め日当日」の窓に入っていて未送信（notice_sent_at が None）の
    締め日設定が対象。全員承認済みの学校はスキップする（ガードは立てない＝窓内に未完了へ
    戻った場合は翌日以降に送る）。
    """
    today = today or _current_jst_date()
    rows = db.execute(
        select(WorkSchoolDeadline, WorkSchoolSetting, User)
        .join(WorkSchoolSetting, WorkSchoolSetting.school_id == WorkSchoolDeadline.school_id)
        .join(User, User.id == WorkSchoolDeadline.school_id)
        .where(
            WorkSchoolSetting.early_check_enabled.is_(True),
            WorkSchoolDeadline.notice_sent_at.is_(None),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
    ).all()

    sent = 0
    for deadline, setting, school in rows:
        days_before = max(0, int(setting.notice_days_before))
        if not (deadline.deadline_date - timedelta(days=days_before) <= today <= deadline.deadline_date):
            continue
        try:
            progress = school_month_progress(db, school, deadline.target_month)
            if progress and progress.all_approved:
                continue  # 完了済み＝完了メールで通知済みのため締め日確認は送らない
            _enqueue_deadline_notice(db, school, deadline, progress)
            deadline.notice_sent_at = datetime.now(timezone.utc)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - 1校の失敗で他校の送信を止めない
            logger.warning(
                "school deadline notice failed: school=%s month=%s: %s",
                school.id, deadline.target_month, exc,
            )
    if sent:
        db.flush()
    return sent
