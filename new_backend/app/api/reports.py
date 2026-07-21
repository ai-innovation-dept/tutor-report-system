import copy
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete as sql_delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.dependencies.auth import get_active_role, get_current_user, has_role, is_admin, require_role
from app.models.shared import Assignment, User
from app.models.work import (
    WorkAssignmentProfile,
    WorkChatMessage,
    WorkChatRead,
    WorkNotification,
    WorkReport,
    WorkReportEvent,
)
from app.schemas.reports import (
    BulkReportAction,
    BulkReportActionOut,
    CloseRequest,
    MonthlySummaryOut,
    ReportCreate,
    ReportEventOut,
    ReportOut,
    ReportPatch,
    SchoolRequestsPatch,
    WorkflowAction,
)
from app.services.report_service import (
    assert_can_view_report,
    assert_tutor_owns,
    can_view_report,
    create_report,
    diff_report_lines,
    get_report_or_404,
    list_reports_for_role,
    list_reports_for_school,
    list_reports_for_tutor,
    office_update_report_data,
    update_report_data,
)
from app.services.notification_service import send_office_edit_notification, send_transition_notifications, send_tutor_edit_notification
from app.services.export_service import build_report_pdf, build_reports_csv, build_reports_pdf
from app.services.workflow_service import execute_transition, separation_locks
from app.workflow.definitions import WorkAction, WorkStatus
from app.workflow.exceptions import CommentRequired, InvalidTransition, PermissionDenied

router = APIRouter(prefix="/api/w/reports", tags=["work-reports"])
stale_router = APIRouter(prefix="/api/w", tags=["work-stale"])

_ACTION_ALLOWED_ROLES: dict[str, set[str]] = {
    WorkAction.SUBMIT: {"tutor"},
    WorkAction.APPROVE: {"school", "sales", "office", "admin_master", "admin_chief"},
    WorkAction.RETURN: {"school", "sales", "office", "admin_master", "admin_chief"},
    WorkAction.SKIP_SCHOOL: {"admin_chief"},
    # 講師起点の差戻し要求。許可・却下はボールを持つロール（遷移表側で個別に制限）
    WorkAction.REQUEST_RETURN: {"tutor"},
    WorkAction.APPROVE_RETURN_REQUEST: {"school", "sales", "office", "admin_master", "admin_chief"},
    WorkAction.DECLINE_RETURN_REQUEST: {"school", "sales", "office", "admin_master", "admin_chief"},
}
_CLOSE_ROLES = {"sales", "office", "admin_master", "admin_chief"}
_TERMINAL_STATUSES = {WorkStatus.APPROVED, WorkStatus.CLOSED}


def _get_assignment(db: Session, assignment_id: uuid.UUID) -> Assignment:
    a = db.get(Assignment, assignment_id)
    if not a or not a.is_active:
        raise HTTPException(status_code=404, detail="assignment not found")
    return a


def _report_scope_stmt(user: User, active_role: str, target_month: str | None = None):
    roles = list(user.roles or []) or ([user.role] if user.role else [])
    stmt = select(WorkReport)
    admin_roles = {"school", "sales", "office", "admin_master", "admin_chief"}
    if active_role == "tutor" or ("tutor" in roles and active_role not in admin_roles):
        stmt = stmt.where(WorkReport.tutor_id == user.id)
    elif active_role not in {"admin_master", "admin_chief"}:
        stmt = stmt.where(WorkReport.current_approver_role == active_role)
    if target_month:
        stmt = stmt.where(WorkReport.target_month == target_month)
    return stmt


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _user_roles(user: User) -> list[str]:
    return list(user.roles or []) or ([user.role] if user.role else [])


def _resolve_actor_role(user: User, active_role: str, requested_role: str | None) -> str:
    actor_role = requested_role or active_role
    if actor_role not in _user_roles(user):
        raise HTTPException(status_code=403, detail="actor_role is not assigned to this user")
    return actor_role


def _ensure_action_role_allowed(action: str, actor_role: str) -> None:
    allowed = _ACTION_ALLOWED_ROLES.get(action)
    if allowed is not None and actor_role not in allowed:
        raise HTTPException(status_code=403, detail="actor_role is not allowed for this action")


def _report_in_role_scope(db: Session, report: WorkReport, user: User, actor_role: str) -> bool:
    return can_view_report(db, report, user, actor_role)


def _stale_stmt(user: User, active_role: str):
    stmt = (
        select(WorkReport)
        .where(WorkReport.target_month < _current_month())
        .where(WorkReport.status.notin_([WorkStatus.APPROVED, WorkStatus.CLOSED]))
    )
    if active_role == "tutor":
        stmt = stmt.where(WorkReport.tutor_id == user.id)
    elif active_role == "school":
        stmt = stmt.join(Assignment, Assignment.id == WorkReport.assignment_id).where(Assignment.parent_id == user.id)
    elif active_role not in {"sales", "office", "admin_master", "admin_chief"}:
        stmt = stmt.where(False)
    return stmt


def _close_report(db: Session, report: WorkReport, actor: User, actor_role: str, close_reason: str) -> WorkReport:
    if not close_reason or not close_reason.strip():
        raise HTTPException(status_code=422, detail="close_reason is required")
    if actor_role not in _CLOSE_ROLES:
        raise HTTPException(status_code=403, detail="actor_role is not allowed to close reports")
    if report.target_month >= _current_month():
        raise HTTPException(status_code=422, detail="current month reports cannot be closed")
    if report.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="terminal reports cannot be closed")

    from_status = report.status
    now = datetime.now(timezone.utc)
    report.status = WorkStatus.CLOSED
    report.current_approver_role = None
    report.closed_at = now
    report.closed_by = actor.id
    report.close_reason = close_reason.strip()
    report.updated_at = now
    db.add(
        WorkReportEvent(
            report_id=report.id,
            actor_id=actor.id,
            action=WorkAction.CLOSE,
            from_status=from_status,
            to_status=WorkStatus.CLOSED,
            comment=report.close_reason,
        )
    )
    db.flush()
    return report


def _numeric_sum(lines: list[dict], key: str) -> int:
    total = 0
    for line in lines:
        try:
            total += int(line.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    return total


def _student_name(report: WorkReport) -> str:
    return report.assignment.student_name if report.assignment else "生徒未設定"


def _tutor_name(report: WorkReport) -> str:
    return report.tutor.display_name if report.tutor else "講師未設定"


def _report_display_name(report: WorkReport) -> str:
    assignment = report.assignment
    if assignment and assignment.parent:
        return assignment.parent.display_name
    meta = (report.form_data or {}).get("meta") or {}
    if meta.get("dispatch_place_name"):
        return meta["dispatch_place_name"]
    return _student_name(report)


# 明細行の同一日付の重複登録ガード（空欄行は対象外）。フロント側でも同条件で入力時にブロックする。
def _assert_no_duplicate_line_dates(form_data: dict) -> None:
    if not isinstance(form_data, dict):
        return
    lines = form_data.get("lines")
    if not isinstance(lines, list):
        return
    seen: set[str] = set()
    for line in lines:
        if not isinstance(line, dict):
            continue
        value = str(line.get("date") or "").strip()
        if not value:
            continue
        if value in seen:
            raise HTTPException(status_code=422, detail=f"同じ日付の行が複数あります（{value}）")
        seen.add(value)


def _undated_line_number(form_data: dict) -> int | None:
    """記入があるのに日付が未入力の明細行の行番号（1はじまり。無ければ None）。

    下書き保存では日付未入力の書きかけ行を許容し、提出（submit）・事務修正の確定時に
    ブロックする。空欄行（全項目未入力）は対象外。フロント側の
    work_report_calc.findUndatedLineIndex と同一ルール。
    """
    if not isinstance(form_data, dict):
        return None
    lines = form_data.get("lines")
    if not isinstance(lines, list):
        return None
    for number, line in enumerate(lines, start=1):
        if not isinstance(line, dict):
            continue
        if str(line.get("date") or "").strip():
            continue
        if any(str(value if value is not None else "").strip() for key, value in line.items() if key != "date"):
            return number
    return None


def _assert_no_undated_lines(form_data: dict, detail_suffix: str) -> None:
    number = _undated_line_number(form_data)
    if number is not None:
        raise HTTPException(status_code=422, detail=f"{number}回目：日付が未入力の行があります。{detail_suffix}")


@router.post("", response_model=ReportOut, status_code=201)
def create(
    payload: ReportCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    assignment = _get_assignment(db, payload.assignment_id)
    if assignment.tutor_id != user.id:
        raise HTTPException(status_code=403, detail="not your assignment")
    _assert_no_duplicate_line_dates(payload.form_data)
    # 要望連絡事項（学校）など他ロールが入力するメタ項目は講師の新規作成では持ち込ませない
    _strip_other_role_meta(payload.form_data)
    # 契約（契約管理で登録した内容）が無い場合は業務連絡表を作成できない
    profile = db.scalar(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.assignment_id == assignment.id,
            WorkAssignmentProfile.is_active.is_(True),
        )
    )
    if not profile:
        raise HTTPException(
            status_code=409,
            detail="契約が未登録のため業務連絡表を作成できません。先に契約管理で登録してください。",
        )
    try:
        report = create_report(db, assignment, user, payload.target_month, payload.form_type, payload.form_data)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="report for this assignment and month already exists")
    db.refresh(report)
    return report


@router.get("", response_model=list[ReportOut])
def list_reports(
    target_month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    if active_role == "tutor":
        return list_reports_for_tutor(db, user.id, target_month)
    if active_role == "school":
        return list_reports_for_school(db, user.id, target_month)
    # 運営スタッフ（事務・営業・経理・管理責任者）は進捗パイプライン全体を見るため全件取得する。
    # 「あなたのタスク」は画面側で current_approver / ステータスにより絞り込む。
    if active_role in {"office", "sales", "admin_master", "admin_chief"}:
        # 進捗タイムライン（承認依頼/承認/差戻し＋コメント＋実行者）を表示するため
        # events と actor、表示名解決用の assignment/tutor をまとめて読み込む（N+1回避）。
        stmt = select(WorkReport).options(
            selectinload(WorkReport.assignment).selectinload(Assignment.parent),
            selectinload(WorkReport.tutor),
            selectinload(WorkReport.events).selectinload(WorkReportEvent.actor),
        )
        if target_month:
            stmt = stmt.where(WorkReport.target_month == target_month)
        return list(db.scalars(stmt.order_by(WorkReport.target_month.desc(), WorkReport.created_at)))
    return list_reports_for_role(db, active_role, target_month)


@router.get("/monthly-summary", response_model=MonthlySummaryOut)
def monthly_summary(
    target_month: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    target_month = target_month or _current_month()
    if active_role not in {"tutor", "admin_master", "admin_chief"}:
        raise HTTPException(status_code=403, detail="monthly summary is only available to tutor/admin_master")
    reports = list(db.scalars(_report_scope_stmt(user, active_role, target_month)))
    status_counts: dict[str, int] = {}
    total_teach = 0
    total_break = 0
    total_fee = 0
    for report in reports:
        status_counts[report.status] = status_counts.get(report.status, 0) + 1
        lines = list((report.form_data or {}).get("lines", []))
        total_teach += _numeric_sum(lines, "teach_minutes")
        total_break += _numeric_sum(lines, "break_minutes")
        total_fee += _numeric_sum(lines, "commute_fee")
    return MonthlySummaryOut(
        target_month=target_month,
        total_reports=len(reports),
        by_status=status_counts,
        pending_action=any(report.current_approver_role == active_role for report in reports),
        status_counts=status_counts,
        total_teach_minutes=total_teach,
        total_break_minutes=total_break,
        total_commute_fee=total_fee,
    )


@router.get("/admin-separation-locks")
def admin_separation_locks(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("office", "sales", "admin_master", "admin_chief")),
):
    """職務分掌のUI制御用：兼務スタッフが事務承認/営業承認を担当済みの講師ID一覧を返す。

    事務承認した講師は営業承認ボタンを、営業承認した講師は事務承認ボタンを無効化するために使う。
    """
    return separation_locks(db, user)


@router.post("/bulk-action", response_model=BulkReportActionOut)
async def bulk_action(
    payload: BulkReportAction,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    actor_role = _resolve_actor_role(user, active_role, payload.actor_role)
    if payload.action not in _ACTION_ALLOWED_ROLES:
        raise HTTPException(status_code=422, detail="unsupported bulk action")
    _ensure_action_role_allowed(payload.action, actor_role)
    if not payload.report_ids:
        raise HTTPException(status_code=422, detail="report_ids is required")

    reports = list(db.scalars(select(WorkReport).where(WorkReport.id.in_(payload.report_ids))))
    reports_by_id = {report.id: report for report in reports}
    processed_ids: list[uuid.UUID] = []
    processed_reports: list[WorkReport] = []
    skip_ids: list[uuid.UUID] = []

    for report_id in payload.report_ids:
        report = reports_by_id.get(report_id)
        if (
            report is None
            or (payload.target_month and report.target_month != payload.target_month)
            or not _report_in_role_scope(db, report, user, actor_role)
            # 日付未入力の記入行を含む報告書は提出しない（単票の提出ガードと同一ルール）
            or (payload.action == WorkAction.SUBMIT and _undated_line_number(report.form_data or {}) is not None)
        ):
            skip_ids.append(report_id)
            continue

        try:
            execute_transition(db, report, user, actor_role, payload.action, payload.comment)
            processed_ids.append(report_id)
            processed_reports.append(report)
        except (PermissionDenied, InvalidTransition, CommentRequired):
            skip_ids.append(report_id)

    db.commit()
    await send_transition_notifications(db, payload.action, processed_reports, user, payload.comment)
    return BulkReportActionOut(
        processed=len(processed_ids),
        skipped=len(skip_ids),
        skip_ids=skip_ids,
        updated=len(processed_ids),
        report_ids=processed_ids,
    )


@router.get("/export")
def export_reports(
    target_month: str,
    assignment_id: uuid.UUID | None = None,
    tutor_id: uuid.UUID | None = None,
    scope: str | None = None,
    format: str = "pdf",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if format not in {"pdf", "csv"}:
        raise HTTPException(status_code=422, detail="format must be pdf or csv")
    if scope and scope not in {"all", "approved_only"}:
        raise HTTPException(status_code=422, detail="scope must be all or approved_only")

    stmt = (
        select(WorkReport)
        .options(
            selectinload(WorkReport.assignment).selectinload(Assignment.parent),
            selectinload(WorkReport.tutor),
        )
        .where(WorkReport.target_month == target_month)
    )
    assignment = db.get(Assignment, assignment_id) if assignment_id else None
    if assignment_id and not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")

    if has_role(user, "tutor"):
        if tutor_id and tutor_id != user.id:
            raise HTTPException(status_code=403, detail="cannot export other tutor reports")
        if assignment and assignment.tutor_id != user.id:
            raise HTTPException(status_code=403, detail="access denied")
        stmt = stmt.where(
            WorkReport.tutor_id == user.id,
            WorkReport.status == WorkStatus.APPROVED,
        )
    elif has_role(user, "school"):
        if assignment and assignment.parent_id != user.id:
            raise HTTPException(status_code=403, detail="access denied")
        stmt = stmt.join(Assignment, WorkReport.assignment_id == Assignment.id).where(
            Assignment.parent_id == user.id,
            WorkReport.status == WorkStatus.APPROVED,
        )
    elif is_admin(user):
        if scope in {"all", "approved_only"} or has_role(user, "admin_chief"):
            stmt = stmt.where(WorkReport.status == WorkStatus.APPROVED)
    else:
        raise HTTPException(status_code=403, detail="not allowed")

    if assignment_id:
        stmt = stmt.where(WorkReport.assignment_id == assignment_id)
    elif tutor_id:
        stmt = stmt.where(WorkReport.tutor_id == tutor_id)

    reports = db.scalars(stmt.order_by(WorkReport.assignment_id, WorkReport.created_at)).all()
    if not reports:
        raise HTTPException(status_code=404, detail="no reports found")

    year, month_str = target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"

    # CSV出力（全講師分の業務連絡表・横持ち）。最終承認済み（approved）を対象月で出力する。
    if format == "csv":
        if assignment_id and assignment and assignment.parent:
            scope_name = assignment.parent.display_name
        elif assignment_id:
            scope_name = _report_display_name(reports[0])
        elif tutor_id:
            tutor = db.get(User, tutor_id)
            scope_name = tutor.display_name if tutor else "講師"
        else:
            scope_name = "全講師"
        csv_name = f"業務連絡表_{scope_name}_{month_label}.csv"
        return Response(
            content=build_reports_csv(list(reports), target_month),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(csv_name)}"},
        )

    if assignment_id:
        if assignment and assignment.parent:
            assignment_name = assignment.parent.display_name
        else:
            assignment_name = _report_display_name(reports[0]) if reports else "生徒"
        filename_base = f"指導実績_{assignment_name}_{month_label}"
    elif tutor_id:
        tutor = db.get(User, tutor_id)
        filename_base = f"指導実績_{tutor.display_name if tutor else '講師'}_全生徒_{month_label}"
    elif has_role(user, "school"):
        filename_base = f"指導実績_{user.display_name}_全生徒_{month_label}"
    else:
        filename_base = f"指導実績_全体_{month_label}"

    if len(reports) == 1:
        report = reports[0]
        content = build_report_pdf(report, _report_display_name(report), _tutor_name(report))
    else:
        content = build_reports_pdf(
            [(report, _report_display_name(report), _tutor_name(report)) for report in reports],
            target_month,
        )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename_base + '.pdf')}"},
    )


@router.get("/{report_id}", response_model=ReportOut)
def get_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    report = get_report_or_404(db, report_id)
    assert_can_view_report(db, report, user, active_role)
    return report


# 契約管理で登録した内容（契約由来）のメタ項目。講師は変更不可。
# requests（要望連絡事項）は期別設定（前期/後期の月時間・週コマ・適用期間）＋契約期間の自動反映欄。
# task_reference（委託業務（契約より））は前期・後期の業務名・ID類のスナップショット。
# contract_period / monthly_minutes_fixed / weekly_lessons は旧形式の報告書の
# スナップショット保全のために残している（新規報告書では使用しない）。
_CONTRACT_LOCKED_META_KEYS = (
    "customer_id",
    "our_staff",
    "dispatch_place_address",
    "work_location",
    "classroom_name",
    "requests",
    "task_reference",
    "contract_period",
    "monthly_minutes_fixed",
    "weekly_lessons",
    "work_content",
    "note_schedule",
)

# 他ロールが入力するメタ項目（講師の保存で消さない・上書きさせない）。
# requests_school（要望連絡事項（学校））は学校専用API（school-requests）だけが更新する。
_OTHER_ROLE_META_KEYS = ("requests_school",)


def _preserve_locked_meta(report: WorkReport, form_data: dict) -> None:
    """講師による編集時、契約由来・他ロール入力のメタ項目を保存済みの値に固定する。

    画面（UI）だけでなくサーバー側でも上書きを防ぎ、生JSON編集や細工した
    リクエストからも契約内容・学校の記入内容を講師が変更できないようにする。
    講師フォームは meta を丸ごと組み立て直して送るため、この保持が無いと
    学校が書いた要望連絡事項（学校）が講師の保存で消える。
    """
    if not isinstance(form_data, dict):
        return
    old_meta = (report.form_data or {}).get("meta") or {}
    new_meta = form_data.get("meta")
    if not isinstance(new_meta, dict):
        return
    for key in _CONTRACT_LOCKED_META_KEYS:
        if key in old_meta:
            new_meta[key] = old_meta[key]
    for key in _OTHER_ROLE_META_KEYS:
        # 保存済みが無ければ講師の送信値も捨てる（講師が学校欄を新規に書き起こせないように）
        if key in old_meta:
            new_meta[key] = old_meta[key]
        else:
            new_meta.pop(key, None)


def _strip_other_role_meta(form_data: dict) -> None:
    """講師による新規作成時、他ロールが入力するメタ項目を落とす（学校欄のなりすまし防止）。"""
    if not isinstance(form_data, dict):
        return
    meta = form_data.get("meta")
    if not isinstance(meta, dict):
        return
    for key in _OTHER_ROLE_META_KEYS:
        meta.pop(key, None)


@router.patch("/{report_id}", response_model=ReportOut)
async def patch_report(
    report_id: uuid.UUID,
    payload: ReportPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """講師本人による報告書編集（下書き・差戻し中のみ）。
    差戻し中の報告書を修正・保存した場合は、差戻した操作者へ差分を通知する
    （事務修正通知 send_office_edit_notification と対になる講師→運営方向の通知）。"""
    report = get_report_or_404(db, report_id)
    assert_tutor_owns(report, user)
    _assert_no_duplicate_line_dates(payload.form_data)
    # 契約管理で登録した内容は講師側で変更させない（保存済みの値を保持）
    _preserve_locked_meta(report, payload.form_data)
    # 差戻し中の保存は通知のため、適用前に差分（修正前→修正後）を算出できるよう旧内容を退避する
    was_returned = report.status == WorkStatus.RETURNED_TO_TUTOR
    old_form_data = copy.deepcopy(report.form_data or {}) if was_returned else None
    update_report_data(db, report, payload.form_data)
    changes = diff_report_lines(report.form_type, old_form_data, payload.form_data) if was_returned else []
    if changes:
        # 「何を何に変えたか」を監査履歴(comment)として保存する（事務修正 office_edit と対の tutor_edit）
        edit_comment = "【修正内容】\n" + "\n".join(f"・{label}：{old} → {new}" for label, old, new in changes)
        db.add(WorkReportEvent(
            report_id=report.id, actor_id=user.id, action="tutor_edit",
            from_status=WorkStatus.RETURNED_TO_TUTOR, to_status=report.status, comment=edit_comment,
        ))
    db.commit()
    db.refresh(report)
    if changes:
        await send_tutor_edit_notification(db, report, user, changes)
    return report


@router.patch("/{report_id}/school-requests", response_model=ReportOut)
def patch_school_requests(
    report_id: uuid.UUID,
    payload: SchoolRequestsPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("school")),
):
    """要望連絡事項（学校）の保存（改修依頼 202607211716-②）。

    学校ロールが自校の報告書に対して meta.requests_school のみを更新する。
    明細・他のメタ項目は受け取らないため、学校が講師の記入内容を書き換えることはできない。
    編集できるのは学校確認待ち（＝学校がボールを持っている間）のみ。対象月は問わない（案B）。
    通知メールは送らない（承認・差戻し時の既存メールに内容が載る）。
    """
    report = get_report_or_404(db, report_id)
    assert_can_view_report(db, report, user, "school")
    if report.status != WorkStatus.AWAITING_SCHOOL:
        raise HTTPException(
            status_code=409,
            detail="要望連絡事項（学校）を入力できるのは学校確認待ちの業務連絡表のみです",
        )
    form_data = copy.deepcopy(report.form_data or {})
    meta = form_data.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    meta["requests_school"] = payload.requests_school.strip()
    form_data["meta"] = meta
    report.form_data = form_data
    report.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)
    return report


@router.patch("/{report_id}/office-edit", response_model=ReportOut)
async def office_edit_report(
    report_id: uuid.UUID,
    payload: ReportPatch,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("office")),
):
    """事務担当による報告書修正。

    既存システムの受付(admin_receiver)による報告書修正と同等の機能。
    報告書の編集は担当者（講師）本人と事務のみが可能で、営業は不可（営業は承認/差戻しのみ）。
    事務確認待ち・営業確認待ち・事務差戻し中の報告書を修正でき、再承認は不要。
    修正内容を監査イベントに記録し、講師・学校へ通知する。
    勤怠区分（有給休暇・欠勤）の取得回数・欠勤回数の管理にも用いる。
    """
    report = get_report_or_404(db, report_id)
    _assert_no_duplicate_line_dates(payload.form_data)
    # 提出済みの報告書を扱うため、記入があるのに日付が未入力の行は保存不可（提出ガードと同一ルール）
    _assert_no_undated_lines(payload.form_data, "日付を入力してください。")
    from_status = report.status
    # 修正前の内容を退避し、適用後に差分（修正前→修正後）を算出する
    old_form_data = copy.deepcopy(report.form_data or {})
    office_update_report_data(db, report, payload.form_data)
    changes = diff_report_lines(report.form_type, old_form_data, payload.form_data)
    has_comment = bool(payload.comment and payload.comment.strip())
    # 既存システムと同様、変更（差分）も連絡事項（コメント）も無い場合は記録も通知も行わない
    if changes or has_comment:
        db.add(
            WorkReportEvent(
                report_id=report.id,
                actor_id=user.id,
                action="office_edit",
                from_status=from_status,
                to_status=report.status,
                comment=payload.comment,
            )
        )
    db.commit()
    db.refresh(report)
    if changes or has_comment:
        await send_office_edit_notification(db, report, payload.comment, changes)
    return report


@router.delete("/{report_id}", status_code=204)
def delete_report(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """学校へ依頼する前（下書き）または講師へ差戻された報告を講師本人が削除する。"""
    report = get_report_or_404(db, report_id)
    assert_tutor_owns(report, user)
    if report.status not in (WorkStatus.DRAFT, WorkStatus.RETURNED_TO_TUTOR):
        raise HTTPException(status_code=409, detail="下書きまたは差戻し中の報告のみ削除できます")
    message_ids = db.scalars(
        select(WorkChatMessage.id).where(WorkChatMessage.report_id == report.id)
    ).all()
    if message_ids:
        db.execute(sql_delete(WorkChatRead).where(WorkChatRead.message_id.in_(message_ids)))
        db.execute(sql_delete(WorkChatMessage).where(WorkChatMessage.id.in_(message_ids)))
    db.execute(sql_delete(WorkReportEvent).where(WorkReportEvent.report_id == report.id))
    db.execute(sql_delete(WorkNotification).where(WorkNotification.report_id == report.id))
    db.delete(report)
    db.commit()


@router.post("/{report_id}/action", response_model=ReportOut)
async def workflow_action(
    report_id: uuid.UUID,
    payload: WorkflowAction,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    report = get_report_or_404(db, report_id)
    notify_action = payload.action
    # 記入があるのに日付が未入力の行を含む報告書は提出できない（下書き保存は許容・提出時にブロック）
    if payload.action == WorkAction.SUBMIT:
        _assert_no_undated_lines(report.form_data or {}, "日付を入力してから提出してください。")
    try:
        actor_role = _resolve_actor_role(user, active_role, payload.actor_role)
        if not _report_in_role_scope(db, report, user, actor_role):
            raise HTTPException(status_code=403, detail="not allowed to access this report")
        if payload.action == WorkAction.CLOSE:
            _close_report(db, report, user, actor_role, payload.comment or "")
        else:
            execute_transition(db, report, user, actor_role, payload.action, payload.comment)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CommentRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.commit()
    await send_transition_notifications(db, notify_action, [report], user, payload.comment)
    db.refresh(report)
    return report


@router.post("/{report_id}/close", response_model=ReportOut)
async def close_report(
    report_id: uuid.UUID,
    payload: CloseRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    actor_role = _resolve_actor_role(user, active_role, None)
    report = get_report_or_404(db, report_id)
    _close_report(db, report, user, actor_role, payload.close_reason)
    db.commit()
    await send_transition_notifications(db, WorkAction.CLOSE, [report], user, payload.close_reason)
    db.refresh(report)
    return report


@router.get("/{report_id}/events", response_model=list[ReportEventOut])
def get_events(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    report = get_report_or_404(db, report_id)
    assert_can_view_report(db, report, user, active_role)
    events = db.scalars(
        select(WorkReportEvent)
        .options(selectinload(WorkReportEvent.actor))
        .where(WorkReportEvent.report_id == report_id)
        .order_by(WorkReportEvent.created_at)
    ).all()
    return list(events)


@router.get("/{report_id}/export")
def export_pdf(
    report_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    report = get_report_or_404(db, report_id)
    assert_can_view_report(db, report, user, active_role)
    assignment = db.scalar(
        select(Assignment)
        .options(selectinload(Assignment.parent))
        .where(Assignment.id == report.assignment_id)
    )
    report.assignment = assignment
    display_name = _report_display_name(report)
    tutor = db.get(User, report.tutor_id)
    tutor_name = tutor.display_name if tutor else "講師"

    year, month_str = report.target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"
    filename = f"指導実績_{display_name}_{month_label}.pdf"

    content = build_report_pdf(report, display_name, tutor_name)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@stale_router.get("/stale-count")
def stale_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    actor_role = _resolve_actor_role(user, active_role, None)
    count = len(list(db.scalars(_stale_stmt(user, actor_role))))
    return {"count": count}


@stale_router.get("/stale-reports", response_model=list[ReportOut])
def stale_reports(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_role: str = Depends(get_active_role),
):
    actor_role = _resolve_actor_role(user, active_role, None)
    if actor_role not in _CLOSE_ROLES:  # _CLOSE_ROLES already includes admin_chief
        raise HTTPException(status_code=403, detail="stale reports are only available to admin roles")
    return list(db.scalars(_stale_stmt(user, actor_role).order_by(WorkReport.target_month, WorkReport.created_at)))
