"""報告書の作成・更新・取得ロジック。"""
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.forms.definitions import get_form
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkReport
from app.workflow.definitions import WorkStatus


def current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


# 勤怠区分（種別）。明細行の "kind" キーで保持する。空＝勤務（既定）。
# 講師フォーム(reports.html)・参照ビュー(report_view.html)・PDF(export_service)で共有する不変キー。
ATTENDANCE_LABELS = {
    "": "勤務",
    "work": "勤務",
    "paid_leave": "有給休暇",
    "absent": "欠勤",
    "personal_reason": "自己都合",
    "school_event": "学校行事",
}


def is_leave_kind(kind) -> bool:
    """有給休暇または欠勤の区分か（勤務時間を一切持たない行）。"""
    return kind in ("paid_leave", "absent")


def is_no_main_duty_kind(kind) -> bool:
    """自己都合または学校行事の区分か（担当時限・担当業務は0固定、副業務等は入力可）。"""
    return kind in ("personal_reason", "school_event")


def attendance_counts(lines) -> dict[str, int]:
    """明細行から種別（有給休暇/欠勤/自己都合/学校行事）ごとの回数と勤務日数を数える。

    種別付きの行は勤務日数に含めない。勤務日数は「勤務区分かつ何らかの記入がある行」。
    """
    paid_leave = 0
    absent = 0
    personal_reason = 0
    school_event = 0
    work_days = 0
    for line in lines or []:
        if not isinstance(line, dict):
            continue
        kind = line.get("kind") or ""
        if kind == "paid_leave":
            paid_leave += 1
        elif kind == "absent":
            absent += 1
        elif kind == "personal_reason":
            personal_reason += 1
        elif kind == "school_event":
            school_event += 1
        elif any(str(value).strip() for key, value in line.items() if key != "kind"):
            work_days += 1
    return {
        "paid_leave": paid_leave,
        "absent": absent,
        "personal_reason": personal_reason,
        "school_event": school_event,
        "work_days": work_days,
    }


def get_report_or_404(db: Session, report_id) -> WorkReport:
    report = db.get(WorkReport, report_id)
    if not report:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="report not found")
    return report


def can_view_report(db: Session, report: WorkReport, user: User, active_role: str) -> bool:
    """選択中のロールで報告書を閲覧できるかを返す。"""
    if active_role == "tutor":
        return report.tutor_id == user.id
    if active_role == "school":
        assignment = report.assignment or db.get(Assignment, report.assignment_id)
        school_id = assignment.parent_id if assignment and assignment.parent_id else db.scalar(
            select(WorkAssignmentProfile.school_id).where(
                WorkAssignmentProfile.assignment_id == report.assignment_id
            )
        )
        return bool(
            school_id == user.id
            and report.status != WorkStatus.DRAFT
        )
    return active_role in {"office", "sales", "admin_master", "admin_chief"}


def assert_can_view_report(db: Session, report: WorkReport, user: User, active_role: str) -> None:
    """一覧と個別APIで共通の報告書閲覧権限を適用する。"""
    if not can_view_report(db, report, user, active_role):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="not allowed to access this report")


def assert_tutor_owns(report: WorkReport, user: User) -> None:
    if report.tutor_id != user.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="not your report")


def create_report(
    db: Session,
    assignment: Assignment,
    tutor: User,
    target_month: str,
    form_type: str,
    form_data: dict,
) -> WorkReport:
    get_form(form_type)  # validates form_type exists
    report = WorkReport(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        target_month=target_month,
        form_type=form_type,
        form_data=form_data,
        status=WorkStatus.DRAFT,
        current_approver_role="tutor",
    )
    db.add(report)
    db.flush()
    return report


def update_report_data(db: Session, report: WorkReport, form_data: dict) -> WorkReport:
    if report.status not in (WorkStatus.DRAFT, WorkStatus.RETURNED_TO_TUTOR, WorkStatus.RETURNED_TO_OFFICE):
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="report cannot be edited in current status")
    report.form_data = form_data
    report.updated_at = datetime.now(timezone.utc)
    return report


# 事務担当が報告書を修正できるステータス。
# 既存システムの受付(admin_receiver)の編集可能3ステータス
# （受付待ち/再鑑待ち/受付差戻し中）に対応する。
OFFICE_EDIT_STATUSES = (
    WorkStatus.AWAITING_OFFICE,
    WorkStatus.AWAITING_SALES,
    WorkStatus.RETURNED_TO_OFFICE,
)


def office_update_report_data(db: Session, report: WorkReport, form_data: dict) -> WorkReport:
    """事務担当による報告書修正。講師の編集フローとは別系統で、再承認は不要・通知のみ。"""
    if report.status not in OFFICE_EDIT_STATUSES:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=409,
            detail="事務が修正できるのは、事務確認待ち・営業確認待ち・事務差戻し中の報告書のみです",
        )
    report.form_data = form_data
    report.updated_at = datetime.now(timezone.utc)
    return report


def _format_cell(value) -> str:
    """差分表示用に明細セルの値を文字列化する。"""
    if value is None or value == "":
        return "（なし）"
    return str(value)


def _format_kind(value) -> str:
    """勤怠区分（種別）の値を表示名（勤務/有給休暇/欠勤）に整形する。"""
    return ATTENDANCE_LABELS.get(value or "", str(value))


def diff_report_lines(
    form_type: str, old_data: dict | None, new_data: dict | None
) -> list[tuple[str, str, str]]:
    """報告書(form_data)の明細(lines)の修正前→修正後の差分を算出する。

    戻り値は (項目名, 修正前, 修正後) のタプルのリスト。
    項目名はフォーム定義のカラム名を用いて「N行目 〇〇」の形式とする。
    既存システム(_collect_changes)に相当するが、新システムは月次の明細行構造のため
    行単位での比較となる（ここが両システムの構造上の差分箇所）。
    """
    try:
        columns = get_form(form_type).columns
    except KeyError:
        columns = ()
    labels = {c.key: c.label for c in columns}
    labels["kind"] = "種別"
    keys_order = [c.key for c in columns]
    # 勤怠区分（種別）は静的フォーム列に含まれないため、差分検出対象へ明示的に加える（日付の直後）。
    if "kind" not in keys_order:
        insert_at = keys_order.index("date") + 1 if "date" in keys_order else 0
        keys_order.insert(insert_at, "kind")

    old_lines = list((old_data or {}).get("lines", []) or [])
    new_lines = list((new_data or {}).get("lines", []) or [])
    changes: list[tuple[str, str, str]] = []

    for index in range(max(len(old_lines), len(new_lines))):
        rownum = index + 1
        old_line = old_lines[index] if index < len(old_lines) else None
        new_line = new_lines[index] if index < len(new_lines) else None
        if old_line is None:
            changes.append((f"{rownum}行目", "（なし）", "（追加）"))
            continue
        if new_line is None:
            changes.append((f"{rownum}行目", "（削除）", "（なし）"))
            continue
        keys = keys_order or sorted(set(old_line) | set(new_line))
        for key in keys:
            if key == "kind":
                old_cell = _format_kind(old_line.get("kind"))
                new_cell = _format_kind(new_line.get("kind"))
            else:
                old_cell = _format_cell(old_line.get(key))
                new_cell = _format_cell(new_line.get(key))
            if old_cell == new_cell:
                continue
            changes.append((f"{rownum}行目 {labels.get(key, key)}", old_cell, new_cell))
    return changes


def list_reports_for_tutor(db: Session, tutor_id, target_month: str | None = None) -> list[WorkReport]:
    stmt = select(WorkReport).where(WorkReport.tutor_id == tutor_id)
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return list(db.scalars(stmt.order_by(WorkReport.target_month.desc())))


def list_reports_for_role(db: Session, role: str, target_month: str | None = None) -> list[WorkReport]:
    stmt = select(WorkReport).where(WorkReport.current_approver_role == role)
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return list(db.scalars(stmt.order_by(WorkReport.target_month.desc(), WorkReport.created_at)))


def list_reports_for_school(db: Session, school_user_id, target_month: str | None = None) -> list[WorkReport]:
    """学校ユーザーに、担当校の提出済み報告だけを返す。"""
    profile_assignments = select(WorkAssignmentProfile.assignment_id).where(
        WorkAssignmentProfile.school_id == school_user_id
    )
    stmt = (
        select(WorkReport)
        .join(Assignment, Assignment.id == WorkReport.assignment_id)
        .where(
            or_(
                Assignment.parent_id == school_user_id,
                and_(
                    Assignment.parent_id.is_(None),
                    WorkReport.assignment_id.in_(profile_assignments),
                ),
            ),
            WorkReport.status != WorkStatus.DRAFT,
        )
    )
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return list(db.scalars(stmt.order_by(WorkReport.target_month.desc(), WorkReport.created_at)))
