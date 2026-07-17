"""学校単位の「契約講師全員の学校承認」完了判定と運営（事務・営業）への通知。

EMPS-2026-0709-01 → 改修 202607161140:
- 即時通知: ある学校に紐づく有効契約の講師全員（「当月授業なし」申請中の講師を除く）の
  当月報告書が学校承認を通過した時点で、事務・営業（office / sales ロールの有効ユーザー全員）へ
  完了メールを送る。最後の1件が承認されるたびに発火する（差戻し後の再承認で全員承認が
  再成立した場合も再送する）。講師の「当月授業なし」申請で全員承認が成立した場合も発火する。
- 月末+N日の進捗ダイジェストメールは 202607161140 で廃止
  （純粋に「契約講師全員の学校承認完了」の通知のみを行う）。

学校確認スキップ（学校ユーザー単位の skip_parent_approval）の学校は対象外。
「当月授業なし」= 講師が講師画面で申請する月単位のフラグ（work_no_lesson_months・全契約対象）。
申請中の講師は報告書の有無・状態を問わず集計の対象外（完了メールには対象外として明記する）。
"""
import calendar
import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkNoLessonMonth, WorkNotification, WorkReport
from app.services.notification_service import _staff_users, enqueue_email_template
from app.workflow.definitions import WorkStatus

logger = logging.getLogger(__name__)

_ALL_APPROVED_TYPE = "school_all_approved"

# 完了メールの宛先ロールと、ロール別の宛名・確認画面パス（202607161140で事務を追加）
_STAFF_RECIPIENT_ROLES = ("office", "sales")
_STAFF_LABELS = {"office": "事務担当者", "sales": "営業担当者"}
_STAFF_QUEUE_PATHS = {"office": "/office/queue", "sales": "/sales/queue"}

# 学校承認を通過済み（現在も有効）とみなすステータス。
# returned_to_office / approved は学校承認後の工程のため「承認済み」に含める。
_SCHOOL_APPROVED_STATUSES = {
    WorkStatus.AWAITING_OFFICE,
    WorkStatus.AWAITING_SALES,
    WorkStatus.APPROVED,
    WorkStatus.RETURNED_TO_OFFICE,
}

# 未承認側の状態ラベル（締め日前確認メールの内訳表示用）
_PENDING_STATUS_LABELS = {
    WorkStatus.DRAFT: "未提出",
    WorkStatus.AWAITING_OFFICE_PRECHECK: "事務事前確認中",
    WorkStatus.AWAITING_SCHOOL: "学校確認待ち",
    WorkStatus.RETURNED_TO_TUTOR: "差戻し中",
    WorkStatus.CLOSED: "打ち切り（クローズ）",
}
_NO_REPORT_LABEL = "未作成"


@dataclass
class TutorProgress:
    tutor: User
    report: WorkReport | None
    approved: bool
    label: str  # 承認済みは「承認済み」、未承認は状態ラベル（未提出/学校確認待ち/未作成 等）


@dataclass
class SchoolMonthProgress:
    school: User
    target_month: str
    entries: list[TutorProgress]
    # 「当月授業なし」申請中の講師（集計対象外）。approved/label は申請が無かった場合の値を保持する
    # （申請が完了成立の決め手だったかの判定と、完了メールの対象外表示に使う）。
    no_lesson_entries: list[TutorProgress] = field(default_factory=list)

    @property
    def approved_entries(self) -> list[TutorProgress]:
        return [e for e in self.entries if e.approved]

    @property
    def pending_entries(self) -> list[TutorProgress]:
        return [e for e in self.entries if not e.approved]

    @property
    def all_approved(self) -> bool:
        # 対象講師（授業なし申請を除く）が1名以上いて、全員承認済みのときのみ成立。
        # 全員が授業なし申請の月は成立しない（通知対象の実績が無いため）。
        return bool(self.entries) and not self.pending_entries


def _month_bounds(target_month: str) -> tuple[date, date]:
    year, month = map(int, target_month.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _record_school_notification(db: Session, user: User, notif_type: str, subject: str, body: str) -> None:
    """学校単位通知のアプリ内ログ。報告書に紐づけない（report_id=None）ことで、
    報告書削除時に消えないログとして機能する。"""
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


def _no_lesson_tutor_ids(db: Session, tutor_ids: list, target_month: str) -> set:
    """指定講師のうち、対象月に「当月授業なし」を申請している講師IDの集合を返す。"""
    if not tutor_ids:
        return set()
    return set(
        db.scalars(
            select(WorkNoLessonMonth.tutor_id).where(
                WorkNoLessonMonth.tutor_id.in_(tutor_ids),
                WorkNoLessonMonth.target_month == target_month,
            )
        )
    )


def school_month_progress(db: Session, school: User, target_month: str) -> SchoolMonthProgress | None:
    """学校×当月の契約講師ごとの学校承認状況を集計する。

    学校確認スキップの学校・有効契約が1件もない学校は対象外（None）。
    「当月授業なし」申請中の講師は entries から外し no_lesson_entries に分ける。
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
    no_lesson_ids = _no_lesson_tutor_ids(db, [p.tutor_id for p in profiles], target_month)

    entries: list[TutorProgress] = []
    no_lesson_entries: list[TutorProgress] = []
    for p in sorted(profiles, key=lambda x: (x.tutor.tutor_no or x.tutor.user_no or "", x.tutor.display_name)):
        report = by_assignment.get(p.assignment_id)
        if report is None:
            entry = TutorProgress(tutor=p.tutor, report=None, approved=False, label=_NO_REPORT_LABEL)
        else:
            approved = report.status in _SCHOOL_APPROVED_STATUSES
            label = "承認済み" if approved else _PENDING_STATUS_LABELS.get(report.status, "その他")
            entry = TutorProgress(tutor=p.tutor, report=report, approved=approved, label=label)
        if p.tutor_id in no_lesson_ids:
            no_lesson_entries.append(entry)
        else:
            entries.append(entry)
    return SchoolMonthProgress(
        school=school, target_month=target_month, entries=entries, no_lesson_entries=no_lesson_entries
    )


# ---------------------------------------------------------------------------
# 即時通知（全員の学校承認が揃った時点で事務・営業へ）
# ---------------------------------------------------------------------------

def _all_approved_subject(school: User, target_month: str) -> str:
    return f"【業務連絡表】学校承認がすべて完了しました（{target_month}分 {school.display_name}）"


async def send_school_all_approved_notifications(db: Session, reports: list[WorkReport]) -> None:
    """学校承認直後の報告書群から、契約講師全員の承認が揃った学校を判定し事務・営業へ通知する。

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


def send_school_all_approved_after_no_lesson(db: Session, tutor: User, target_month: str) -> int:
    """講師の「当月授業なし」申請で全員承認が成立した学校を判定し、事務・営業へ完了メールを送る。

    申請した講師が実際に完了成立の決め手（＝申請前は未承認扱い）だった学校のみ送信する。
    すでに全員承認済みだった学校（申請講師の報告書も承認済み等）へは重複送信しない。
    戻り値は通知した学校数。通知の失敗は主処理（申請の保存）を止めない。
    """
    school_ids: list = []
    for profile in db.scalars(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id,
            WorkAssignmentProfile.is_active.is_(True),
        )
    ):
        if profile.school_id not in school_ids:
            school_ids.append(profile.school_id)

    count = 0
    for school_id in school_ids:
        try:
            school = db.get(User, school_id)
            progress = school_month_progress(db, school, target_month)
            if not progress or not progress.all_approved:
                continue
            entry = next((e for e in progress.no_lesson_entries if e.tutor.id == tutor.id), None)
            if entry is None or entry.approved:
                continue  # この講師は元々未達要因ではない＝申請前から完了済み（通知済み）のため再送しない
            _enqueue_all_approved_mail(db, progress)
            count += 1
        except Exception as exc:  # noqa: BLE001 - 通知の失敗は申請の保存を止めない
            logger.warning(
                "school all-approved notification (no-lesson) failed: school=%s month=%s: %s",
                school_id, target_month, exc,
            )
    db.commit()
    return count


def _enqueue_all_approved_mail(db: Session, progress: SchoolMonthProgress) -> None:
    subject = _all_approved_subject(progress.school, progress.target_month)
    no_lesson_block = ""
    if progress.no_lesson_entries:
        lines = "\n".join(f"・{_tutor_label(e.tutor)}" for e in progress.no_lesson_entries)
        no_lesson_block = f"\n\n【対象外（当月授業なし申請）】\n{lines}"
    base_context = {
        "school_label": _school_label(progress.school),
        "target_month": progress.target_month,
        "tutor_count": len(progress.entries),
        "tutor_lines": "\n".join(f"・{_tutor_label(e.tutor)}" for e in progress.entries),
        "no_lesson_block": no_lesson_block,
        "base_url": settings.NEW_BASE_URL.rstrip("/"),
    }
    body_log = f"{progress.school.display_name}の{progress.target_month}分は契約講師全員の学校承認が完了しました。"
    for role in _STAFF_RECIPIENT_ROLES:
        context = base_context | {
            "recipient_label": _STAFF_LABELS[role],
            "queue_path": _STAFF_QUEUE_PATHS[role],
        }
        for staff in _staff_users(db, role):
            enqueue_email_template(db, staff.email, subject, "notify_school_all_approved.txt", context)
            _record_school_notification(db, staff, _ALL_APPROVED_TYPE, subject, body_log)
