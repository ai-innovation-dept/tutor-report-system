from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.reports import _report_out
from app.core.rbac import has_role
from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.schemas import CloseReportRequest, ReportOut
from app.services.report_service import close_report, get_stale_reports

router = APIRouter(tags=["stale-reports"])


def _require_stale_staff(user: User) -> None:
    if not (
        has_role(user, "admin_receiver")
        or has_role(user, "admin_reviewer")
        or has_role(user, "admin_master")
        or has_role(user, "admin_chief")
    ):
        raise HTTPException(status_code=403, detail="not allowed")


@router.get("/api/stale-count")
def stale_count(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    reports = get_stale_reports(db, current_user=current_user)
    return {"count": len(reports)}


@router.get("/api/stale-reports", response_model=list[ReportOut])
def stale_reports(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_stale_staff(current_user)
    return [_report_out(db, report, current_user) for report in get_stale_reports(db, current_user=None)]


@router.post("/api/reports/{report_id}/close", response_model=ReportOut)
def close_report_endpoint(
    report_id: UUID,
    body: CloseReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_stale_staff(current_user)
    report = close_report(report_id, body.close_reason, current_user, db)
    return _report_out(db, report, current_user)
