# === Phase 4: 指導報告書 CRUD START ===
import io
import os
from collections import defaultdict
from datetime import date
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.rbac import has_role, is_admin, require_role
from app.core.time import get_current_jst_month, month_string
from app.database import get_db
from app.deps import get_current_user, get_report_for_user
from app.models import Assignment, ChatMessage, ChatRead, LessonReport, Notification, ReportAction, ReportEvent, ReportStatus, User
from app.schemas import GroupAdminEditIn, ReportCreate, ReportEventOut, ReportOut, ReportPatch
from app.services.workflow_service import notify_report_modified, notify_tutor_report_edited, separation_locks

STATUS_RANK = {
    ReportStatus.draft.value: 0,
    ReportStatus.returned_to_tutor.value: 0,
    ReportStatus.awaiting_parent_approval.value: 1,
    ReportStatus.parent_approved.value: 2,
    ReportStatus.submitted_to_admin.value: 3,
    ReportStatus.received.value: 4,
    ReportStatus.re_reviewed.value: 5,
    ReportStatus.admin_approved.value: 6,
    ReportStatus.closed.value: 7,
}

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _current_month() -> str:
    return get_current_jst_month()


def _report_out(db: Session, report: LessonReport, user: User) -> ReportOut:
    events = db.scalars(
        select(ReportEvent)
        .options(selectinload(ReportEvent.actor))
        .where(
            ReportEvent.report_id == report.id,
        )
        .order_by(ReportEvent.created_at)
    ).all()
    last = events[-1].action if events else None
    last_return_event = next(
        (
            event
            for event in reversed(events)
            if event.comment
            and event.action
            in {"parent_return", "return_from_receiver", "return_from_reviewer", "return_from_master"}
        ),
        None,
    )
    unread = db.scalar(
        select(func.count(ChatMessage.id))
        .where(ChatMessage.report_id == report.id, ChatMessage.sender_id != user.id)
        .outerjoin(ChatRead, (ChatRead.message_id == ChatMessage.id) & (ChatRead.user_id == user.id))
        .where(ChatRead.message_id.is_(None))
    ) or 0
    out = ReportOut.model_validate(report)
    out.last_event = last
    if last_return_event:
        out.last_return_comment = last_return_event.comment
        out.last_return_at = last_return_event.created_at
    out.unread_count = unread
    out.student_name = report.assignment.student_name if report.assignment else None
    out.skip_parent_approval = bool(report.parent and report.parent.skip_parent_approval)
    out.tutor_name = report.tutor.display_name if report.tutor else None
    out.tutor_no = report.tutor.user_no if report.tutor else None
    out.parent_name = report.parent.display_name if report.parent else None
    out.parent_no = report.parent.user_no if report.parent else None
    out.closed_by_name = report.closed_by_user.display_name if report.closed_by_user else None
    out.events = [
        ReportEventOut(
            action=event.action,
            actor_name=event.actor.display_name if event.actor else None,
            actor_role=event.actor.role if event.actor else None,
            created_at=event.created_at,
            comment=event.comment,
        )
        for event in events
    ]
    return out


def _teaching_minutes(report: LessonReport) -> int:
    start = report.start_time.hour * 60 + report.start_time.minute
    end = report.end_time.hour * 60 + report.end_time.minute
    return max(0, end - start - (report.break_minutes or 0))


def _duration_label(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}時間{mins}分"
    if hours:
        return f"{hours}時間"
    return f"{mins}分"


def _latest(values) -> object | None:
    filtered = [value for value in values if value is not None]
    return max(filtered) if filtered else None


def _earliest(values) -> object | None:
    filtered = [value for value in values if value is not None]
    return min(filtered) if filtered else None


def _monthly_phase(reports: list[LessonReport]) -> str:
    statuses = [report.status for report in reports]
    ranks = [STATUS_RANK.get(status, 0) for status in statuses]
    if any(status == ReportStatus.returned_to_tutor.value for status in statuses):
        return "returned"
    if statuses and all(status == ReportStatus.closed.value for status in statuses):
        return "closed"
    if statuses and all(status == ReportStatus.admin_approved.value for status in statuses):
        return "completed"
    if statuses and all(rank >= STATUS_RANK[ReportStatus.submitted_to_admin.value] for rank in ranks):
        return "submitted_to_admin"
    if statuses and all(rank >= STATUS_RANK[ReportStatus.parent_approved.value] for rank in ranks):
        return "parent_approved"
    if statuses and all(rank >= STATUS_RANK[ReportStatus.awaiting_parent_approval.value] for rank in ranks):
        return "awaiting_parent"
    return "recording"


# 同一生徒（assignment）×同一指導日の重複登録ガード。クローズ済みは無効分として対象外。
def _duplicate_lesson_date_exists(db: Session, tutor_id: UUID, assignment_id: UUID, lesson_date: date, exclude_report_id: UUID | None = None) -> bool:
    stmt = select(func.count(LessonReport.id)).where(
        LessonReport.tutor_id == tutor_id,
        LessonReport.assignment_id == assignment_id,
        LessonReport.lesson_date == lesson_date,
        LessonReport.status != ReportStatus.closed.value,
    )
    if exclude_report_id is not None:
        stmt = stmt.where(LessonReport.id != exclude_report_id)
    return (db.scalar(stmt) or 0) > 0


@router.post("", response_model=ReportOut)
def create_report(payload: ReportCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "tutor":
        raise HTTPException(status_code=403, detail="only tutors can create reports")
    if payload.end_time <= payload.start_time:
        raise HTTPException(status_code=422, detail="終了時刻は開始時刻より後の時刻を指定してください")
    assignment = db.get(Assignment, payload.assignment_id)
    if not assignment or assignment.tutor_id != user.id or not assignment.is_active:
        raise HTTPException(status_code=403, detail="assignment access denied")
    target_month = month_string(payload.lesson_date)
    current_month = _current_month()
    if target_month != current_month:
        raise HTTPException(status_code=400, detail="当月分の報告書のみ作成できます")
    existing_approved = db.scalar(
        select(func.count(LessonReport.id)).where(
            LessonReport.tutor_id == user.id,
            LessonReport.assignment_id == payload.assignment_id,
            LessonReport.target_month == current_month,
            LessonReport.status == ReportStatus.admin_approved.value,
        )
    ) or 0
    if existing_approved > 0:
        raise HTTPException(status_code=409, detail="当月分はすでに最終承認済みです。追加修正が必要な場合は運営に差戻しを依頼してください")
    existing_in_progress = db.scalar(
        select(func.count(LessonReport.id)).where(
            LessonReport.tutor_id == user.id,
            LessonReport.assignment_id == payload.assignment_id,
            LessonReport.target_month == current_month,
            LessonReport.status.notin_([
                ReportStatus.draft.value,
                ReportStatus.returned_to_tutor.value,
                ReportStatus.admin_approved.value,
                ReportStatus.closed.value,
            ]),
        )
    ) or 0
    if existing_in_progress > 0:
        raise HTTPException(status_code=409, detail="当月分の報告書がすでに進行中です")
    # 月単位の状態チェックを通過した後に、同一生徒×同一指導日の重複を確認する
    # （最終承認済み・進行中の場合はより具体的な上記メッセージを優先する）
    if _duplicate_lesson_date_exists(db, user.id, payload.assignment_id, payload.lesson_date):
        raise HTTPException(status_code=409, detail="同じ指導日の報告書がすでに登録されています")
    report = LessonReport(
        **payload.model_dump(),
        tutor_id=user.id,
        parent_id=assignment.parent_id,
        target_month=target_month,
        status=ReportStatus.draft.value,
    )
    db.add(report)
    db.add(ReportEvent(report=report, actor_id=user.id, action="create", to_status=ReportStatus.draft.value))
    db.commit()
    db.refresh(report)
    return _report_out(db, report, user)


@router.get("", response_model=list[ReportOut])
def list_reports(status: str | None = None, target_month: str | None = None, assignment_id: UUID | None = None, tutor_id: UUID | None = None, parent_id: UUID | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    stmt = select(LessonReport).options(selectinload(LessonReport.assignment), selectinload(LessonReport.tutor)).order_by(LessonReport.lesson_date.desc())
    if user.role == "tutor":
        stmt = stmt.where(LessonReport.tutor_id == user.id)
    elif user.role == "parent":
        # スキップ保護者は承認には関与しないが、最終承認済み(admin_approved)は閲覧・PDF取得できるようにする。
        parent_statuses = (
            [ReportStatus.admin_approved.value]
            if user.skip_parent_approval
            else [
                ReportStatus.awaiting_parent_approval.value,
                ReportStatus.returned_to_tutor.value,
                ReportStatus.parent_approved.value,
                ReportStatus.admin_approved.value,
            ]
        )
        stmt = stmt.join(Assignment, LessonReport.assignment_id == Assignment.id).where(
            LessonReport.parent_id == user.id,
            LessonReport.status.in_(parent_statuses),
        )
    elif not user.role.startswith("admin_"):
        raise HTTPException(status_code=403, detail="not allowed")
    if status:
        stmt = stmt.where(LessonReport.status == status)
    if target_month:
        stmt = stmt.where(LessonReport.target_month == target_month)
    if assignment_id:
        stmt = stmt.where(LessonReport.assignment_id == assignment_id)
    if tutor_id and (user.role == "parent" or user.role.startswith("admin_")):
        stmt = stmt.where(LessonReport.tutor_id == tutor_id)
    if parent_id and user.role.startswith("admin_"):
        stmt = stmt.where(LessonReport.parent_id == parent_id)
    return [_report_out(db, row, user) for row in db.scalars(stmt).all()]


@router.get("/monthly-summary")
def monthly_summary(tutor_id: UUID | None = None, target_month: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    target_tutor_id = tutor_id or user.id
    if user.role == "tutor" and target_tutor_id != user.id:
        raise HTTPException(status_code=403, detail="cannot view other tutor summary")
    if user.role != "tutor" and not user.role.startswith("admin_"):
        raise HTTPException(status_code=403, detail="not allowed")

    stmt = select(LessonReport).where(LessonReport.tutor_id == target_tutor_id)
    if target_month:
        stmt = stmt.where(LessonReport.target_month == target_month)
    reports = db.scalars(stmt.order_by(LessonReport.target_month.desc(), LessonReport.lesson_date.asc(), LessonReport.start_time.asc())).all()
    grouped: dict[str, list[LessonReport]] = defaultdict(list)
    for report in reports:
        grouped[report.target_month].append(report)

    summaries = []
    for month, items in grouped.items():
        phase = _monthly_phase(items)
        report_items = [_report_out(db, report, user).model_dump(mode="json") for report in items]
        submitted_to_parent_dates = [report.submitted_to_parent_at for report in items]
        parent_approved_dates = [report.parent_approved_at for report in items]
        submitted_to_admin_dates = [report.submitted_to_admin_at for report in items]
        admin_approved_dates = [report.admin_approved_at for report in items]
        received_dates = [report.received_at for report in items]
        re_reviewed_dates = [report.re_reviewed_at for report in items]
        summaries.append(
            {
                "target_month": month,
                "total_count": len(items),
                "total_minutes": sum(_teaching_minutes(report) for report in items),
                "phase": phase,
                "submitted_to_parent_at": _latest(submitted_to_parent_dates),
                "first_submitted_to_parent_at": _earliest(submitted_to_parent_dates),
                "parent_approved_at": _latest(parent_approved_dates),
                "submitted_to_admin_at": _latest(submitted_to_admin_dates),
                "received_at": _latest(received_dates),
                "re_reviewed_at": _latest(re_reviewed_dates),
                "admin_approved_at": _latest(admin_approved_dates),
                "has_returned": any(report.status == ReportStatus.returned_to_tutor.value for report in items),
                "can_submit_to_parent": all(report.status in {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value} for report in items),
                "can_submit_to_admin": all(report.status == ReportStatus.parent_approved.value for report in items),
                "is_completed": phase == "completed",
                "reports": report_items,
                "counts_by_status": {status: sum(1 for report in items if report.status == status) for status in sorted({report.status for report in items})},
            }
        )
    return summaries


@router.get("/export")
def export_reports(
    target_month: str,
    assignment_id: UUID | None = None,
    tutor_id: UUID | None = None,
    scope: str | None = None,
    format: str = "pdf",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if format != "pdf":
        raise HTTPException(status_code=422, detail="format must be pdf")
    if scope and scope not in {"all", "approved_only"}:
        raise HTTPException(status_code=422, detail="scope must be all or approved_only")

    stmt = (
        select(LessonReport)
        .options(selectinload(LessonReport.assignment), selectinload(LessonReport.tutor), selectinload(LessonReport.parent))
        .where(LessonReport.target_month == target_month)
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
            LessonReport.tutor_id == user.id,
            LessonReport.status == ReportStatus.admin_approved.value,
        )
    elif has_role(user, "parent"):
        if assignment and assignment.parent_id != user.id:
            raise HTTPException(status_code=403, detail="access denied")
        stmt = stmt.where(
            LessonReport.parent_id == user.id,
            LessonReport.status == ReportStatus.admin_approved.value,
        )
    elif is_admin(user):
        if scope in {"all", "approved_only"}:
            stmt = stmt.where(LessonReport.status == ReportStatus.admin_approved.value)
    else:
        raise HTTPException(status_code=403, detail="not allowed")

    if assignment_id:
        stmt = stmt.where(LessonReport.assignment_id == assignment_id)
    elif tutor_id:
        stmt = stmt.where(LessonReport.tutor_id == tutor_id)

    reports = db.scalars(stmt.order_by(LessonReport.assignment_id, LessonReport.lesson_date, LessonReport.start_time)).all()
    if not reports:
        raise HTTPException(status_code=404, detail="no reports found")

    year, month_str = target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"
    if assignment_id:
        filename_base = f"指導実績_{assignment.student_name}_{month_label}"
    elif tutor_id:
        tutor = db.get(User, tutor_id)
        filename_base = f"指導実績_{tutor.display_name if tutor else '講師'}_全生徒_{month_label}"
    elif has_role(user, "parent"):
        filename_base = f"指導実績_{user.display_name}_全生徒_{month_label}"
    else:
        filename_base = f"指導実績_全体_{month_label}"

    if is_admin(user) or has_role(user, "tutor"):
        stamps = _approval_stamps(db, reports)
    elif has_role(user, "parent"):
        stamps = None  # parent PDF: 承認印エリアを表示しない
    else:
        stamps = {"受付": None, "再鑑": None, "管理者": None}
    content = _build_reports_pdf(db, reports, target_month, stamps)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename_base + '.pdf')}"},
    )


_WD = ["日", "月", "火", "水", "木", "金", "土"]
_PDF_FONT_NAME = "JapaneseReportFont"
_PDF_FONT_REGISTERED = False


def _student_name(report: LessonReport) -> str:
    return report.assignment.student_name if report.assignment else "生徒未設定"


def _tutor_name(report: LessonReport) -> str:
    return report.tutor.display_name if report.tutor else "講師未設定"


def _tutor_label(report: LessonReport) -> str:
    # PDF表示用：講師名にユーザーID(user_no)を併記する（例 大橋悟史（10003））。
    if not report.tutor:
        return "講師未設定"
    name = report.tutor.display_name
    return f"{name}（{report.tutor.user_no}）" if report.tutor.user_no else name


def _month_label(target_month: str) -> str:
    year, month_str = target_month.split("-")
    return f"{year}年{int(month_str):02d}月"


def _report_date_label(report: LessonReport) -> str:
    wd = _WD[(report.lesson_date.weekday() + 1) % 7]
    return f"{report.lesson_date.month}月{report.lesson_date.day}日（{wd}）"


def _pdf_font_paths() -> list[str]:
    return [
        os.environ.get("PDF_JP_FONT_PATH", ""),
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/NotoSansJP-VF.ttf",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
    ]


def _register_pdf_font() -> str:
    global _PDF_FONT_REGISTERED
    if _PDF_FONT_REGISTERED:
        return _PDF_FONT_NAME
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail="reportlab is not installed") from exc

    for path in _pdf_font_paths():
        if path and os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(_PDF_FONT_NAME, path))
                _PDF_FONT_REGISTERED = True
                return _PDF_FONT_NAME
            except Exception:
                continue
    raise HTTPException(status_code=500, detail="Japanese PDF font is not installed")


def _approval_stamps(db: Session, reports: list[LessonReport]) -> dict[str, tuple[str, str, object] | None]:
    """受付再鑑管理者の承認情報を取得して返す"""
    report_ids = [report.id for report in reports]
    action_to_role = {
        ReportAction.receive.value: ("受付", "admin_receiver"),
        ReportAction.re_review.value: ("再鑑", "admin_reviewer"),
        ReportAction.admin_approve.value: ("管理者", "admin_master"),
    }
    stamps = {"受付": None, "再鑑": None, "管理者": None}
    if not report_ids:
        return stamps

    for action, (role_label, _) in action_to_role.items():
        row = db.execute(
            select(ReportEvent, User)
            .join(User, ReportEvent.actor_id == User.id)
            .where(
                ReportEvent.report_id.in_(report_ids),
                ReportEvent.action == action,
            )
            .order_by(ReportEvent.created_at.desc())
            .limit(1)
        ).first()
        if row:
            event, actor = row
            stamps[role_label] = (actor.display_name, role_label, event.created_at)
    return stamps


_COMPANY_FOOTER = "株式会社イスト　〒151-0053　東京都渋谷区代々木1-35-4　代々木クリスタルビル5階　tel.03-4446-2600（代）"


def _hours_label(minutes: int) -> str:
    """指導時間数を0.5時間単位で表示（120分→「2」、150分→「2.5」）。"""
    hours = round(minutes / 30) / 2
    return str(int(hours)) if hours == int(hours) else f"{hours:.1f}"


def _room_period_label(report: LessonReport) -> str:
    """在室した時間帯（休憩等の時間）。例：17:10～19:20（休憩10分）"""
    period = f"{report.start_time.strftime('%H:%M')}～{report.end_time.strftime('%H:%M')}"
    return f"{period}（休憩{report.break_minutes or 0}分）"


def _confirmation_grid_cells(no: int, report: LessonReport | None) -> list[str]:
    """明細1行分（回数・指導日・曜日・在室した時間帯・指導時間数）。報告が無ければ空欄。"""
    if report is None:
        return [str(no), "", "", "", ""]
    weekday = _WD[(report.lesson_date.weekday() + 1) % 7]
    return [
        str(no),
        f"{report.lesson_date.month}/{report.lesson_date.day}",
        weekday,
        _room_period_label(report),
        f"{_hours_label(_teaching_minutes(report))} 時間",
    ]


def _confirmation_stamp(role_label: str, stamp, font_name: str):
    """受付/再鑑/管理者の電子承認印（朱色の二重丸）をフロアブルで返す。未承認は空欄。"""
    from reportlab.graphics.shapes import Circle, Drawing, String
    from reportlab.lib import colors

    size = 50
    drawing = Drawing(size, size)
    if not stamp:
        return drawing
    approver_name, _, approved_at = stamp
    red = colors.HexColor("#c81e1e")
    center = size / 2
    drawing.add(Circle(center, center, 23, strokeColor=red, fillColor=None, strokeWidth=1.2))
    drawing.add(Circle(center, center, 17, strokeColor=red, fillColor=None, strokeWidth=1.0))
    if approved_at:
        drawing.add(String(center, center + 8, f"{approved_at.month}/{approved_at.day}", fontName=font_name, fontSize=6, fillColor=red, textAnchor="middle"))
    drawing.add(String(center, center - 2, role_label, fontName=font_name, fontSize=7, fillColor=red, textAnchor="middle"))
    drawing.add(String(center, center - 12, approver_name[:4], fontName=font_name, fontSize=6, fillColor=red, textAnchor="middle"))
    return drawing


def _draw_confirmation_banner(canvas, doc, font_name: str) -> None:
    """左端の縦帯（黒地・白縦書き「指導時間確認票」「報告用」＋提出期限の注記）を描画。"""
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    _, page_h = doc.pagesize
    band_w = 13 * mm
    center_x = band_w / 2
    canvas.saveState()
    canvas.setFillColor(colors.black)
    canvas.rect(0, 0, band_w, page_h, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.white)
    canvas.setFont(font_name, 13)
    y = page_h - 22 * mm
    for ch in "指導時間確認票":
        canvas.drawCentredString(center_x, y, ch)
        y -= 15
    y -= 6 * mm
    canvas.setLineWidth(0.8)
    canvas.rect(2 * mm, y - 34, band_w - 4 * mm, 36, stroke=1, fill=0)
    canvas.setFont(font_name, 9)
    box_y = y - 4
    for ch in "報告用":
        canvas.drawCentredString(center_x, box_y, ch)
        box_y -= 11
    canvas.setFont(font_name, 6.5)
    note = "※翌月1日までにご提出ください"
    note_y = 8 * mm + (len(note) - 1) * 8
    for ch in note:
        canvas.drawCentredString(center_x, note_y, ch)
        note_y -= 8
    canvas.restoreState()


def _build_reports_pdf(db: Session, reports: list[LessonReport], target_month: str, stamps: dict[str, tuple[str, str, object] | None]) -> bytes:
    """全ロール共通の「指導時間確認票」PDF（A4横）。assignment×月ごとに1ページ。"""
    font_name = _register_pdf_font()
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail="reportlab is not installed") from exc

    note_style = ParagraphStyle("note", fontName=font_name, fontSize=9, leading=12)
    footer_style = ParagraphStyle("footer", fontName=font_name, fontSize=8, leading=11, alignment=TA_CENTER)
    stamps_map = stamps or {}

    grouped: dict[UUID, list[LessonReport]] = defaultdict(list)
    for report in reports:
        grouped[report.assignment_id].append(report)
    group_items = sorted(grouped.values(), key=lambda items: (_student_name(items[0]), _tutor_name(items[0])))

    page_size = landscape(A4)
    left_margin, right_margin = 18 * mm, 8 * mm
    content_w = page_size[0] - left_margin - right_margin
    half_w = content_w / 2
    wide_col = half_w - (30 + 56 + 26 + 52)
    grid_widths = [30, 56, 26, wide_col, 52] * 2

    year, month_str = target_month.split("-")
    year_month = f"{year}年 {int(month_str)}月分"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=page_size,
        leftMargin=left_margin, rightMargin=right_margin, topMargin=12 * mm, bottomMargin=8 * mm,
        title="指導時間確認票",
    )

    story = []
    for group_index, items in enumerate(group_items):
        if group_index:
            story.append(PageBreak())
        items = sorted(items, key=lambda r: (r.lesson_date, r.start_time.strftime("%H:%M")))
        first = items[0]
        tutor = first.tutor
        parent = first.parent
        tutor_no = tutor.user_no if tutor and tutor.user_no else ""
        member_no = parent.user_no if parent and parent.user_no else ""
        parent_name = parent.display_name if parent else ""
        total_minutes = sum(_teaching_minutes(report) for report in items)

        # ヘッダー：講師名 / 講師No. / 合計時間数
        header = Table(
            [["講師名", tutor.display_name if tutor else "", "講師No.", tutor_no, "合計時間数", f"{_hours_label(total_minutes)} 時間"]],
            colWidths=[22 * mm, 80 * mm, 24 * mm, 48 * mm, 30 * mm, content_w - 204 * mm],
            rowHeights=[11 * mm],
        )
        header.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 13),
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#e8e8e8")),
            ("BACKGROUND", (2, 0), (2, 0), colors.HexColor("#e8e8e8")),
            ("BACKGROUND", (4, 0), (4, 0), colors.HexColor("#e8e8e8")),
            ("ALIGN", (2, 0), (5, 0), "CENTER"),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
        ]))

        # 明細グリッド（回数1-10／11-20）
        grid_rows = [["回数", "指導日", "曜日", "在室した時間帯（休憩等の時間）", "指導時間数"] * 2]
        for i in range(10):
            left = _confirmation_grid_cells(i + 1, items[i] if i < len(items) else None)
            right = _confirmation_grid_cells(i + 11, items[i + 10] if i + 10 < len(items) else None)
            grid_rows.append(left + right)
        grid = Table(grid_rows, colWidths=grid_widths, rowHeights=[7 * mm] + [7.4 * mm] * 10)
        grid.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("FONTSIZE", (0, 1), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ]))

        # 下部：左半分＝月計・会員番号・生徒名・保護者名／右半分＝受付・再鑑・管理者の電子印。
        # グリッド中央の仕切り線に揃うよう左右をそれぞれ half_w 幅にし、外側テーブルの余白は0にする。
        left_info = Table(
            [
                ["年月分", year_month],
                ["月計", f"{len(items)} 回　{_hours_label(total_minutes)} 時間"],
                [Paragraph("上記指導日時・時間数に相違ありません。", note_style), ""],
                [Paragraph(f"会員番号　{member_no}　　生徒名　{_student_name(first)}　　保護者名　{parent_name}", note_style), ""],
            ],
            colWidths=[26 * mm, half_w - 26 * mm],
            rowHeights=[7 * mm, 7 * mm, 7 * mm, 7 * mm],
        )
        left_info.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BACKGROUND", (0, 0), (0, 1), colors.HexColor("#e8e8e8")),
            ("SPAN", (0, 2), (1, 2)),
            ("SPAN", (0, 3), (1, 3)),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))

        # 承認フロー変更（再鑑承認＝最終承認）により、新フローの承認印は受付・再鑑の2欄。
        # 旧フローで管理者承認済みの報告書のみ、履歴として管理者印を含む3欄で出力する。
        stamp_labels = ["受付", "再鑑"] + (["管理者"] if stamps_map.get("管理者") else [])
        stamp_signers = {"受付": "受付者", "再鑑": "再鑑者", "管理者": "管理者"}
        stamp_w = (half_w - 3 * mm) / len(stamp_labels)
        stamps_table = Table(
            [
                stamp_labels,
                [_confirmation_stamp(stamp_signers[label], stamps_map.get(label), font_name) for label in stamp_labels],
            ],
            colWidths=[stamp_w] * len(stamp_labels), rowHeights=[6 * mm, 22 * mm],
        )
        stamps_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
        ]))

        # 左ブロックと押印枠は独立させ、間を3mm（明細グリッドと下部表の間隔と同じ）空ける
        sign = Table([[left_info, "", stamps_table]], colWidths=[half_w, 3 * mm, half_w - 3 * mm])
        sign.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

        story.extend([
            header,
            Spacer(1, 3 * mm),
            grid,
            Spacer(1, 3 * mm),
            sign,
            Spacer(1, 2 * mm),
            Paragraph(_COMPANY_FOOTER, footer_style),
        ])

    banner = lambda canvas, doc: _draw_confirmation_banner(canvas, doc, font_name)
    doc.build(story, onFirstPage=banner, onLaterPages=banner)
    return buf.getvalue()


@router.get("/admin-separation-locks")
def admin_separation_locks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 職務分掌のUI制御用：現在のスタッフが受付/再鑑を担当済みの報告書IDを返す。
    # 同一報告書を受付した人はその報告書の再鑑ボタンを、再鑑した人は受付ボタンを無効化するために使う。
    if not user.role.startswith("admin_"):
        raise HTTPException(status_code=403, detail="not allowed")
    return separation_locks(db, user)


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _report_out(db, get_report_for_user(report_id, user, db), user)


@router.patch("/{report_id}", response_model=ReportOut)
async def patch_report(report_id: UUID, payload: ReportPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = get_report_for_user(report_id, user, db)
    if user.role != "tutor" or report.tutor_id != user.id:
        raise HTTPException(status_code=403, detail="report cannot be edited")
    if report.target_month != _current_month():
        raise HTTPException(status_code=400, detail="当月分の報告書のみ作成できます")
    if report.status not in {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value}:
        raise HTTPException(status_code=409, detail="only draft or returned reports can be edited")
    data = payload.model_dump(exclude_unset=True)
    # 差戻し中の報告書の修正は、差戻した運営担当へ通知する。適用すると旧値が失われるため、変更前に差分を取得する。
    was_returned = report.status == ReportStatus.returned_to_tutor.value
    changes = _collect_changes(report, data) if was_returned else []
    for key, value in data.items():
        setattr(report, key, value)
    if report.start_time >= report.end_time:
        raise HTTPException(status_code=422, detail="終了時刻は開始時刻より後の時刻を指定してください")
    if "lesson_date" in data:
        report.target_month = month_string(report.lesson_date)
        if report.target_month != _current_month():
            raise HTTPException(status_code=400, detail="当月分の報告書のみ作成できます")
        if _duplicate_lesson_date_exists(db, report.tutor_id, report.assignment_id, report.lesson_date, exclude_report_id=report.id):
            raise HTTPException(status_code=409, detail="同じ指導日の報告書がすでに登録されています")
    # 差戻し中の修正は「何を何に変えたか」を監査履歴(comment)として保存する（action=tutor_edit）。
    # それ以外（下書き編集・変更なし）は従来どおり action=update（差分なし）で記録する。
    if was_returned and changes:
        edit_comment = "【修正内容】\n" + "\n".join(f"・{label}：{old} → {new}" for label, old, new in changes)
        db.add(ReportEvent(
            report_id=report.id, actor_id=user.id, action="tutor_edit",
            from_status=ReportStatus.returned_to_tutor.value, to_status=report.status, comment=edit_comment,
        ))
    else:
        db.add(ReportEvent(report_id=report.id, actor_id=user.id, action="update", from_status=report.status, to_status=report.status))
    db.commit()
    db.refresh(report)
    # 差戻し中の報告書を講師が修正・保存したら、差戻した操作者へ通知（受付編集通知 notify_report_modified の対）。
    # 変更が無い保存（コメントのみ等）では送らない。再提出時の通知は従来どおり別途行われる。
    if was_returned and changes:
        await notify_tutor_report_edited(db, report, changes, user)
    return _report_out(db, report, user)


# 受付が修正できるのは「受付の手元にある」3状態のみ（承認依頼が届いた／受領済み／再鑑・管理者から差戻し）。
ADMIN_EDIT_STATUSES = {
    ReportStatus.submitted_to_admin.value,
    ReportStatus.received.value,
    ReportStatus.returned_to_receiver.value,
}
_EDIT_FIELD_LABELS = {
    "lesson_date": "指導日",
    "start_time": "開始時刻",
    "end_time": "終了時刻",
    "break_minutes": "休憩時間",
    "subject": "科目",
    "content": "指導内容",
}


def _format_field_value(field: str, value) -> str:
    if value is None or value == "":
        return "（なし）"
    if field in {"start_time", "end_time"}:
        return value.strftime("%H:%M")
    if field == "break_minutes":
        return f"{value}分"
    if field == "lesson_date":
        return value.isoformat()
    return str(value)


def _collect_changes(report: LessonReport, data: dict) -> list[tuple[str, str, str]]:
    """修正前後で変化した項目を (項目名, 変更前, 変更後) のリストで返す（適用前に呼ぶこと）。"""
    changes: list[tuple[str, str, str]] = []
    for field, new_value in data.items():
        label = _EDIT_FIELD_LABELS.get(field)
        if not label:
            continue
        old_value = getattr(report, field)
        if old_value == new_value:
            continue
        changes.append((label, _format_field_value(field, old_value), _format_field_value(field, new_value)))
    return changes


# 受付による報告（生徒×講師×対象月）単位の一括修正。既存の一括操作（admin-receive-bulk 等）と
# 同じく POST・単一セグメントのパスにすることで PATCH /{report_id} とのルート衝突を避ける。
@router.post("/admin-edit-bulk", response_model=list[ReportOut])
async def admin_edit_bulk(
    payload: GroupAdminEditIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin_receiver")),
):
    """受付担当による報告（生徒×講師×対象月）単位の一括修正。
    その月の全指導日を1画面でまとめて編集する。講師の編集フロー(patch_report)とは別系統で、
    再承認は不要・ステータスは不変。変更/コメントがあれば履歴1件＋講師・保護者へ通知1通。"""
    if not payload.lines:
        raise HTTPException(status_code=422, detail="編集対象がありません")
    reports = db.scalars(
        select(LessonReport).where(
            LessonReport.assignment_id == payload.assignment_id,
            LessonReport.tutor_id == payload.tutor_id,
            LessonReport.target_month == payload.target_month,
        )
    ).all()
    by_id = {report.id: report for report in reports}

    all_changes: list[tuple[str, str, str]] = []   # 通知メール用（日付つき差分）
    changed_labels: list[str] = []                  # 履歴コメント用（項目名のユニーク集合）
    changed_reports: list[LessonReport] = []
    for line in payload.lines:
        report = by_id.get(line.id)
        if report is None:
            raise HTTPException(status_code=404, detail="対象の報告書が見つかりません")
        if report.status not in ADMIN_EDIT_STATUSES:
            raise HTTPException(status_code=409, detail="受付が修正できるのは、承認依頼中・受領済み・差戻し中の報告書のみです")
        data = {
            "lesson_date": line.lesson_date,
            "start_time": line.start_time,
            "end_time": line.end_time,
            "break_minutes": line.break_minutes,
            "subject": line.subject,
            "content": line.content,
        }
        changes = _collect_changes(report, data)
        if not changes:
            continue
        for key, value in data.items():
            setattr(report, key, value)
        # 受付は過去月の訂正もあり得るため当月制限は課さない。対象月のみ指導日に追従させる。
        report.target_month = month_string(report.lesson_date)
        date_label = f"{line.lesson_date.month}/{line.lesson_date.day}"
        for label, old, new in changes:
            all_changes.append((f"{date_label} {label}", old, new))
            if label not in changed_labels:
                changed_labels.append(label)
        changed_reports.append(report)

    has_comment = bool(payload.comment and payload.comment.strip())
    if not changed_reports and not has_comment:
        # 変更もコメントも無ければ履歴・通知を残さない（新システムの事務編集と同挙動）。
        return [_report_out(db, report, user) for report in reports]

    event_comment = "修正項目：" + "、".join(changed_labels) if changed_labels else "修正コメントを追加"
    # 履歴イベントは「変更のあった報告書」（コメントのみなら編集可能な全報告書）に同一コメントで残す。
    # ダッシュボードの統合タイムラインは同一(action/操作者/時刻/コメント)を1件に集約表示する。
    event_targets = changed_reports or [r for r in reports if r.status in ADMIN_EDIT_STATUSES]
    for report in event_targets:
        db.add(ReportEvent(
            report_id=report.id, actor_id=user.id, action=ReportAction.receiver_edit.value,
            from_status=report.status, to_status=report.status, comment=event_comment,
        ))
    db.commit()
    for report in reports:
        db.refresh(report)
    sample = (changed_reports or event_targets or reports)[0]
    await notify_report_modified(db, sample, all_changes, user, payload.comment)
    return [_report_out(db, report, user) for report in reports]


@router.delete("/{report_id}")
def delete_report(report_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = get_report_for_user(report_id, user, db)
    if user.role != "tutor" or report.tutor_id != user.id:
        raise HTTPException(status_code=403, detail="only draft or returned reports can be deleted")
    if report.status not in {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value}:
        raise HTTPException(status_code=409, detail="only draft or returned reports can be deleted")
    message_ids = db.scalars(select(ChatMessage.id).where(ChatMessage.report_id == report.id)).all()
    if message_ids:
        db.query(ChatRead).filter(ChatRead.message_id.in_(message_ids)).delete(synchronize_session=False)
    db.query(ChatMessage).filter(ChatMessage.report_id == report.id).delete(synchronize_session=False)
    db.query(ReportEvent).filter(ReportEvent.report_id == report.id).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.report_id == report.id).delete()
    db.delete(report)
    db.commit()
    return {"status": "ok"}
# === Phase 4 END ===
