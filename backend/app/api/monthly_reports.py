# === 指導月報 API START ===
"""指導月報（原本_月報.pdf 準拠）の取得・作成/更新 API。

- 講師: 自分の担当の月報を承認依頼前（下書き・差戻し中）に作成・更新できる。
- 保護者: 自分の子（assignment.parent_id）の月報を参照できる（保護者記入欄は承認APIで記入）。
- 運営（受付/再鑑/管理者/管理責任者）: 全件参照。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.rbac import has_role, is_admin
from app.database import get_db
from app.deps import get_current_user
from app.models import Assignment, MonthlyReport, User
from app.schemas import (
    MonthlyReportAssignmentOut,
    MonthlyReportIn,
    MonthlyReportOut,
    MonthlyReportOverviewOut,
)
from app.services.monthly_report_service import (
    MOCK_SUBJECTS,
    SCHOOL_SUBJECTS,
    editable_state,
    get_monthly_report,
    month_reports,
    normalize_form_data,
)

router = APIRouter(prefix="/api/monthly-reports", tags=["monthly-reports"])


def _validate_month(target_month: str) -> str:
    parts = target_month.split("-")
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and 1 <= int(parts[1]) <= 12 and len(parts[0]) == 4:
        return f"{parts[0]}-{int(parts[1]):02d}"
    raise HTTPException(status_code=422, detail="target_month must be YYYY-MM")


def _teaching_minutes(report) -> int:
    start = report.start_time.hour * 60 + report.start_time.minute
    end = report.end_time.hour * 60 + report.end_time.minute
    return max(0, end - start - (report.break_minutes or 0))


@router.get("/overview", response_model=MonthlyReportOverviewOut)
def monthly_report_overview(
    target_month: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """講師の月報作成画面用：担当ごとの自動入力項目・編集可否・報告書からの自動反映値・保存済み月報。"""
    if not has_role(user, "tutor"):
        raise HTTPException(status_code=403, detail="tutor only")
    target_month = _validate_month(target_month)
    assignments = db.scalars(
        select(Assignment)
        .options(selectinload(Assignment.tutor), selectinload(Assignment.parent))
        .where(Assignment.tutor_id == user.id, Assignment.is_active.is_(True))
        .order_by(Assignment.student_name)
    ).all()
    items: list[MonthlyReportAssignmentOut] = []
    for assignment in assignments:
        reports = month_reports(db, assignment.id, target_month)
        editable, lock_reason = editable_state(reports)
        monthly = get_monthly_report(db, assignment.id, target_month)
        lesson_days = sorted({r.lesson_date.day for r in reports})
        total_minutes = sum(_teaching_minutes(r) for r in reports)
        items.append(
            MonthlyReportAssignmentOut(
                assignment_id=assignment.id,
                student_name=assignment.student_name,
                parent_name=assignment.parent_name,
                parent_no=assignment.parent_no,
                tutor_name=assignment.tutor_name,
                tutor_no=assignment.tutor_no,
                editable=editable,
                lock_reason=lock_reason,
                lesson_days=lesson_days,
                total_minutes=total_minutes,
                report=MonthlyReportOut.model_validate(monthly) if monthly else None,
            )
        )
    return MonthlyReportOverviewOut(
        target_month=target_month,
        mock_subjects=MOCK_SUBJECTS,
        school_subjects=SCHOOL_SUBJECTS,
        assignments=items,
    )


@router.get("", response_model=list[MonthlyReportOut])
def list_monthly_reports(
    target_month: str | None = None,
    assignment_id: UUID | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """ロール別スコープで月報を取得する（講師=自分の担当、保護者=自分の子、運営=全件）。

    target_month 省略時は全月分（画面のPDFボタン表示判定用。1担当×1月で1行のため件数は小さい）。
    """
    stmt = select(MonthlyReport).options(selectinload(MonthlyReport.assignment))
    if target_month:
        stmt = stmt.where(MonthlyReport.target_month == _validate_month(target_month))
    if assignment_id:
        stmt = stmt.where(MonthlyReport.assignment_id == assignment_id)
    if has_role(user, "tutor"):
        stmt = stmt.where(MonthlyReport.tutor_id == user.id)
    elif has_role(user, "parent"):
        stmt = stmt.where(MonthlyReport.parent_id == user.id)
    elif not is_admin(user):
        raise HTTPException(status_code=403, detail="not allowed")
    return list(db.scalars(stmt))


@router.put("/{assignment_id}/{target_month}", response_model=MonthlyReportOut)
def upsert_monthly_report(
    assignment_id: UUID,
    target_month: str,
    payload: MonthlyReportIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """講師本人による月報の作成・更新（承認依頼前のみ）。保護者記入欄はここでは変更できない。"""
    if not has_role(user, "tutor"):
        raise HTTPException(status_code=403, detail="tutor only")
    target_month = _validate_month(target_month)
    assignment = db.get(Assignment, assignment_id)
    if not assignment or not assignment.is_active:
        raise HTTPException(status_code=404, detail="assignment not found")
    if assignment.tutor_id != user.id:
        raise HTTPException(status_code=403, detail="not your assignment")

    editable, lock_reason = editable_state(month_reports(db, assignment_id, target_month))
    if not editable:
        raise HTTPException(status_code=409, detail=f"この月の指導月報は{lock_reason}")

    grade = (payload.grade or "").strip()[:50]
    form_data = normalize_form_data(payload.form_data)
    monthly = get_monthly_report(db, assignment_id, target_month)
    if monthly is None:
        monthly = MonthlyReport(
            assignment_id=assignment_id,
            tutor_id=user.id,
            parent_id=assignment.parent_id,
            target_month=target_month,
            grade=grade,
            form_data=form_data,
        )
        db.add(monthly)
    else:
        monthly.grade = grade
        monthly.form_data = form_data
        monthly.parent_id = assignment.parent_id
    db.commit()
    db.refresh(monthly)
    return monthly
# === 指導月報 API END ===
