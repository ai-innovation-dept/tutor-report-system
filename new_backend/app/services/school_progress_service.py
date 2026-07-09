"""学校単位の「契約講師全員の学校承認」進捗の集計と営業への通知。

EMPS-2026-0709-01:
- 即時通知: ある学校に紐づく有効契約の講師全員の当月報告書が学校承認を通過した時点で、
  営業（sales ロール全員）へ完了メールを送る。最後の1件が承認されるたびに発火する
  （差戻し後の再承認で全員承認が再成立した場合も再送する）。
- 締切進捗メール: 月末+N日（NEW_SCHOOL_PROGRESS_DAYS_AFTER_MONTH_END）にちょうど当たる日に
  1回だけ、全員承認が揃っていない学校の進捗（承認済み/未承認の講師とその状態）を
  営業へ1通のダイジェストで送る。全員承認済みの学校は即時通知済みのため対象外。

学校確認スキップ（学校ユーザー単位の skip_parent_approval）の学校は両方とも対象外。
「当月授業なし」= 当月の報告書レコードが存在しない講師（未作成）を指す。
"""
import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkNotification, WorkReport
from app.services.notification_service import _staff_users, enqueue_email_template
from app.workflow.definitions import WorkStatus

logger = logging.getLogger(__name__)

_ALL_APPROVED_TYPE = "school_all_approved"
_PROGRESS_TYPE = "school_monthly_progress"

# 学校承認を通過済み（現在も有効）とみなすステータス。
# returned_to_office / approved は学校承認後の工程のため「承認済み」に含める。
_SCHOOL_APPROVED_STATUSES = {
    WorkStatus.AWAITING_OFFICE,
    WorkStatus.AWAITING_SALES,
    WorkStatus.APPROVED,
    WorkStatus.RETURNED_TO_OFFICE,
}

# 未承認側の状態ラベル（進捗メールの内訳表示用）
_PENDING_STATUS_LABELS = {
    WorkStatus.DRAFT: "未提出",
    WorkStatus.AWAITING_OFFICE_PRECHECK: "事務事前確認中",
    WorkStatus.AWAITING_SCHOOL: "学校確認待ち",
    WorkStatus.RETURNED_TO_TUTOR: "差戻し中",
    WorkStatus.CLOSED: "打ち切り（クローズ）",
}
_NO_REPORT_LABEL = "当月授業なし"


@dataclass
class TutorProgress:
    tutor: User
    report: WorkReport | None
    approved: bool
    label: str  # 承認済みは「承認済み」、未承認は状態ラベル（未提出/学校確認待ち/当月授業なし 等）


@dataclass
class SchoolMonthProgress:
    school: User
    target_month: str
    entries: list[TutorProgress]

    @property
    def approved_entries(self) -> list[TutorProgress]:
        return [e for e in self.entries if e.approved]

    @property
    def pending_entries(self) -> list[TutorProgress]:
        return [e for e in self.entries if not e.approved]

    @property
    def all_approved(self) -> bool:
        return bool(self.entries) and not self.pending_entries


def _month_bounds(target_month: str) -> tuple[date, date]:
    year, month = map(int, target_month.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _current_jst_date() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


def _record_school_notification(db: Session, user: User, notif_type: str, subject: str, body: str) -> None:
    """学校単位通知のアプリ内ログ。報告書に紐づけない（report_id=None）ことで、
    報告書削除時に消えず、進捗メールの重複送信防止ログとしても機能する。"""
    db.add(
        WorkNotification(
            user_id=user.id,
            report_id=None,
            channel="email",
            type=notif_type,
            subject=subject,
            body=body,
            sent_at=None,
        )
    )


def _tutor_label(tutor: User) -> str:
    no = tutor.tutor_no or tutor.user_no
    return f"{tutor.display_name}（講師No.{no}）" if no else tutor.display_name


def _school_label(school: User) -> str:
    return f"{school.display_name}（学校No.{school.user_no}）" if school.user_no else school.display_name


def _active_profiles_for_school(db: Session, school_id, target_month: str) -> list[WorkAssignmentProfile]:
    """当月に有効な契約（is_active かつ契約期間が当月に重なる）を返す。講師が退会済みの契約は除く。"""
    first, last = _month_bounds(target_month)
    profiles = db.scalars(
        select(WorkAssignmentProfile)
        .options(selectinload(WorkAssignmentProfile.tutor))
        .where(
            WorkAssignmentProfile.school_id == school_id,
            WorkAssignmentProfile.is_active.is_(True),
        )
    ).all()
    result = []
    for p in profiles:
        if p.contract_start and p.contract_start > last:
            continue
        if p.contract_end and p.contract_end < first:
            continue
        if not p.tutor or not p.tutor.is_active or p.tutor.deleted_at:
            continue
        result.append(p)
    return result


def school_month_progress(db: Session, school: User, target_month: str) -> SchoolMonthProgress | None:
    """学校×当月の契約講師ごとの学校承認状況を集計する。

    学校確認スキップの学校・有効契約が1件もない学校は対象外（None）。
    """
    if not school or not school.is_active or school.deleted_at or school.skip_parent_approval:
        return None
    profiles = _active_profiles_for_school(db, school.id, target_month)
    if not profiles:
        return None

    reports = db.scalars(
        select(WorkReport).where(
            WorkReport.assignment_id.in_([p.assignment_id for p in profiles]),
            WorkReport.target_month == target_month,
        )
    ).all()
    by_assignment = {r.assignment_id: r for r in reports}

    entries: list[TutorProgress] = []
    for p in sorted(profiles, key=lambda x: (x.tutor.tutor_no or x.tutor.user_no or "", x.tutor.display_name)):
        report = by_assignment.get(p.assignment_id)
        if report is None:
            entries.append(TutorProgress(tutor=p.tutor, report=None, approved=False, label=_NO_REPORT_LABEL))
            continue
        approved = report.status in _SCHOOL_APPROVED_STATUSES
        label = "承認済み" if approved else _PENDING_STATUS_LABELS.get(report.status, "その他")
        entries.append(TutorProgress(tutor=p.tutor, report=report, approved=approved, label=label))
    return SchoolMonthProgress(school=school, target_month=target_month, entries=entries)


# ---------------------------------------------------------------------------
# 即時通知（全員の学校承認が揃った時点で営業へ）
# ---------------------------------------------------------------------------

def _all_approved_subject(school: User, target_month: str) -> str:
    return f"【業務連絡表】学校承認がすべて完了しました（{target_month}分 {school.display_name}）"


async def send_school_all_approved_notifications(db: Session, reports: list[WorkReport]) -> None:
    """学校承認直後の報告書群から、契約講師全員の承認が揃った学校を判定し営業へ通知する。

    学校承認の遷移（approve: awaiting_school → awaiting_office）で呼ばれる前提。
    一括承認では同一学校×月につき1通にまとめる。通知の失敗は主処理を止めない。
    """
    seen: set[tuple[str, str]] = set()
    for report in reports:
        if report.status != WorkStatus.AWAITING_OFFICE:
            continue
        assignment = report.assignment or db.get(Assignment, report.assignment_id)
        if not assignment or not assignment.parent_id:
            continue
        key = (str(assignment.parent_id), report.target_month)
        if key in seen:
            continue
        seen.add(key)
        try:
            school = db.get(User, assignment.parent_id)
            progress = school_month_progress(db, school, report.target_month)
            if not progress or not progress.all_approved:
                continue
            _enqueue_all_approved_mail(db, progress)
        except Exception as exc:  # noqa: BLE001 - 通知の失敗は承認処理を止めない
            logger.warning("school all-approved notification failed: school=%s month=%s: %s", key[0], key[1], exc)
    db.commit()


def _enqueue_all_approved_mail(db: Session, progress: SchoolMonthProgress) -> None:
    subject = _all_approved_subject(progress.school, progress.target_month)
    context = {
        "school_label": _school_label(progress.school),
        "target_month": progress.target_month,
        "tutor_count": len(progress.entries),
        "tutor_lines": "\n".join(f"・{_tutor_label(e.tutor)}" for e in progress.entries),
        "base_url": settings.NEW_BASE_URL.rstrip("/"),
    }
    for sales in _staff_users(db, "sales"):
        enqueue_email_template(db, sales.email, subject, "notify_school_all_approved.txt", context)
        _record_school_notification(
            db, sales, _ALL_APPROVED_TYPE, subject,
            f"{progress.school.display_name}の{progress.target_month}分は契約講師全員の学校承認が完了しました。",
        )


# ---------------------------------------------------------------------------
# 締切進捗メール（月末+N日に1回・未完了の学校のみ・営業へダイジェスト1通）
# ---------------------------------------------------------------------------

def _progress_subject(target_month: str) -> str:
    return f"【業務連絡表】学校承認の進捗のお知らせ（{target_month}分）"


def _progress_target_month(today: date, days_after: int) -> str | None:
    """today が「ある月の末日 + N日」にちょうど当たる場合、その対象月(YYYY-MM)を返す。"""
    candidate = today - timedelta(days=days_after)
    last_day = calendar.monthrange(candidate.year, candidate.month)[1]
    if candidate.day != last_day:
        return None
    return candidate.strftime("%Y-%m")


def _progress_already_sent(db: Session, target_month: str) -> bool:
    row = db.scalars(
        select(WorkNotification).where(
            WorkNotification.type == _PROGRESS_TYPE,
            WorkNotification.subject == _progress_subject(target_month),
        )
    ).first()
    return row is not None


def _school_block(progress: SchoolMonthProgress) -> str:
    lines = [
        f"■ {_school_label(progress.school)}　承認済み {len(progress.approved_entries)}/{len(progress.entries)}名",
        "　【承認済み】",
    ]
    if progress.approved_entries:
        lines.extend(f"　・{_tutor_label(e.tutor)}" for e in progress.approved_entries)
    else:
        lines.append("　（なし）")
    lines.append("　【未承認】")
    lines.extend(f"　・{_tutor_label(e.tutor)}：{e.label}" for e in progress.pending_entries)
    return "\n".join(lines)


def enqueue_monthly_school_progress(db: Session, today: date | None = None) -> int:
    """月末+N日に、全員承認が揃っていない学校の進捗を営業へダイジェスト送信する。

    送信は対象月につき1回（WorkNotification のログで重複送信を防ぐ）。
    サーバー停止等で当日を逃した月は自動送信しない（必要なら today を指定して手動実行する）。
    戻り値は掲載した学校数（送信なしは 0）。
    """
    today = today or _current_jst_date()
    days_after = max(0, int(settings.NEW_SCHOOL_PROGRESS_DAYS_AFTER_MONTH_END))
    target_month = _progress_target_month(today, days_after)
    if not target_month:
        return 0
    if _progress_already_sent(db, target_month):
        return 0

    schools = db.scalars(
        select(User).where(
            User.role == "school",
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.skip_parent_approval.is_(False),
        )
    ).all()

    pending_blocks: list[str] = []
    for school in sorted(schools, key=lambda s: (s.user_no or "", s.display_name)):
        progress = school_month_progress(db, school, target_month)
        if not progress or progress.all_approved:
            continue  # 全員承認済みの学校は即時通知済みのため対象外
        pending_blocks.append(_school_block(progress))

    if not pending_blocks:
        return 0

    subject = _progress_subject(target_month)
    context = {
        "target_month": target_month,
        "school_count": len(pending_blocks),
        "school_blocks": "\n\n".join(pending_blocks),
        "base_url": settings.NEW_BASE_URL.rstrip("/"),
    }
    for sales in _staff_users(db, "sales"):
        enqueue_email_template(db, sales.email, subject, "notify_school_monthly_progress.txt", context)
        _record_school_notification(
            db, sales, _PROGRESS_TYPE, subject,
            f"{target_month}分の学校承認が未完了の学校: {len(pending_blocks)}校",
        )
    db.flush()
    return len(pending_blocks)
