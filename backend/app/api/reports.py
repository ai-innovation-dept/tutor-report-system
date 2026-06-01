# === Phase 4: 指導報告書 CRUD START ===
import io
import os
from collections import defaultdict
from urllib.parse import quote
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.rbac import has_role, is_admin
from app.core.time import get_current_jst_month, month_string
from app.database import get_db
from app.deps import get_current_user, get_report_for_user
from app.models import Assignment, ChatMessage, ChatRead, LessonReport, Notification, ReportAction, ReportEvent, ReportStatus, User
from app.schemas import ReportCreate, ReportEventOut, ReportOut, ReportPatch

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
    out.skip_parent_approval = bool(report.assignment and report.assignment.skip_parent_approval)
    out.tutor_name = report.tutor.display_name if report.tutor else None
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
        stmt = stmt.join(Assignment, LessonReport.assignment_id == Assignment.id).where(
            LessonReport.parent_id == user.id,
            Assignment.skip_parent_approval.is_(False),
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


def _draw_approval_stamps_grid(canvas, doc, stamps: dict[str, tuple[str, str, object] | None] | None, font_name: str) -> None:
    if stamps is None:
        return  # parent ロール：承認印エリアを描画しない
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    roles = ["受付", "再鑑", "管理者"]
    role_stamp_labels = {"受付": "受付者", "再鑑": "再鑑者", "管理者": "管理者"}
    box_width = 28 * mm
    box_height = 25 * mm
    box_gap = 0
    x_right = doc.pagesize[0] - doc.rightMargin
    y_top = doc.pagesize[1] - 8 * mm
    y_bottom = y_top - box_height
    canvas.saveState()
    for i, role_label in enumerate(reversed(roles)):
        x = x_right - (i + 1) * box_width - i * box_gap
        canvas.setStrokeColor(colors.black)
        canvas.setLineWidth(0.5)
        canvas.rect(x, y_bottom, box_width, box_height, stroke=1, fill=0)
        canvas.setFillColor(colors.black)
        canvas.setFont(font_name, 7)
        canvas.drawCentredString(x + box_width / 2, y_top - 5 * mm, role_label)

        stamp = stamps.get(role_label)
        if not stamp:
            continue

        approver_name, _, approved_at = stamp
        approved_label = f"{approved_at.year}年{approved_at.month}月{approved_at.day}日" if approved_at else ""
        center_x = x + box_width / 2
        center_y = y_bottom + 12 * mm
        stamp_color = colors.HexColor("#c81e1e")
        canvas.setStrokeColor(stamp_color)
        canvas.setFillColor(stamp_color)
        canvas.setLineWidth(1.2)
        canvas.circle(center_x, center_y, 9 * mm, stroke=1, fill=0)
        canvas.circle(center_x, center_y, 6.5 * mm, stroke=1, fill=0)
        canvas.setFont(font_name, 6)
        canvas.drawCentredString(center_x, center_y + 4 * mm, approved_label)
        canvas.setFont(font_name, 8)
        canvas.drawCentredString(center_x, center_y, role_stamp_labels[role_label])
        canvas.setFont(font_name, 8)
        canvas.drawCentredString(center_x, center_y - 5 * mm, approver_name[:5])
    canvas.restoreState()


def _build_reports_pdf(db: Session, reports: list[LessonReport], target_month: str, stamps: dict[str, tuple[str, str, object] | None]) -> bytes:
    font_name = _register_pdf_font()
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail="reportlab is not installed") from exc

    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    styles["Title"].fontSize = 15
    styles["Heading2"].fontSize = 11
    styles["Normal"].fontSize = 9

    grouped: dict[UUID, list[LessonReport]] = defaultdict(list)
    for report in reports:
        grouped[report.assignment_id].append(report)
    group_items = sorted(grouped.values(), key=lambda items: (_student_name(items[0]), _tutor_name(items[0])))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=40 * mm,
        bottomMargin=22 * mm,
        title="指導実績",
    )
    story = []
    headers = ["指導日", "在室時間", "休憩", "指導時間数", "科目"]
    for group_index, items in enumerate(group_items):
        if group_index:
            story.append(PageBreak())
        first = items[0]
        story.append(Paragraph("指導実績", styles["Title"]))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(f"生徒名：{_student_name(first)}　講師名：{_tutor_name(first)}　対象月：{_month_label(target_month)}", styles["Normal"]))
        story.append(Spacer(1, 5 * mm))
        rows = [headers]
        for report in items:
            rows.append(
                [
                    _report_date_label(report),
                    f"{report.start_time.strftime('%H:%M')} - {report.end_time.strftime('%H:%M')}",
                    f"{report.break_minutes or 0}分",
                    _duration_label(_teaching_minutes(report)),
                    report.subject or "",
                ]
            )
        rows.append(["合計", "", "", _duration_label(sum(_teaching_minutes(report) for report in items)), ""])
        table = Table(rows, colWidths=[34 * mm, 35 * mm, 24 * mm, 32 * mm, 45 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), font_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#222222")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (2, 1), (3, -1), "RIGHT"),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7f7f7")),
                ]
            )
        )
        story.append(table)
    doc.build(
        story,
        onFirstPage=lambda canvas, doc: _draw_approval_stamps_grid(canvas, doc, stamps, font_name),
        onLaterPages=lambda canvas, doc: _draw_approval_stamps_grid(canvas, doc, stamps, font_name),
    )
    return buf.getvalue()


@router.get("/{report_id}", response_model=ReportOut)
def get_report(report_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _report_out(db, get_report_for_user(report_id, user, db), user)


@router.patch("/{report_id}", response_model=ReportOut)
def patch_report(report_id: UUID, payload: ReportPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    report = get_report_for_user(report_id, user, db)
    if user.role != "tutor" or report.tutor_id != user.id:
        raise HTTPException(status_code=403, detail="report cannot be edited")
    if report.target_month != _current_month():
        raise HTTPException(status_code=400, detail="当月分の報告書のみ作成できます")
    if report.status not in {ReportStatus.draft.value, ReportStatus.returned_to_tutor.value}:
        raise HTTPException(status_code=409, detail="only draft or returned reports can be edited")
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(report, key, value)
    if report.start_time >= report.end_time:
        raise HTTPException(status_code=422, detail="終了時刻は開始時刻より後の時刻を指定してください")
    if "lesson_date" in data:
        report.target_month = month_string(report.lesson_date)
        if report.target_month != _current_month():
            raise HTTPException(status_code=400, detail="当月分の報告書のみ作成できます")
    db.add(ReportEvent(report_id=report.id, actor_id=user.id, action="update", from_status=report.status, to_status=report.status))
    db.commit()
    db.refresh(report)
    return _report_out(db, report, user)


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
