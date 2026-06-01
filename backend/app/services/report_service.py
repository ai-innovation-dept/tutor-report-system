from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.time import get_current_jst
from app.models import LessonReport, ReportStatus, User


TERMINAL_STATUSES = {ReportStatus.admin_approved.value, ReportStatus.closed.value}


def get_stale_reports(db: Session) -> list[LessonReport]:
    """
    先月以前の未処理報告書を返す。
    バナー、一覧、バッチ通知の全箇所がこの関数を呼ぶ。
    """
    current_month = get_current_jst().strftime("%Y-%m")
    stmt = (
        select(LessonReport)
        .options(selectinload(LessonReport.assignment), selectinload(LessonReport.tutor), selectinload(LessonReport.parent))
        .where(
            LessonReport.target_month < current_month,
            LessonReport.status.notin_(TERMINAL_STATUSES),
        )
        .order_by(LessonReport.target_month.asc(), LessonReport.lesson_date.asc(), LessonReport.start_time.asc())
    )
    return list(db.scalars(stmt).all())


def close_report(
    report_id: UUID,
    close_reason: str,
    closed_by_user: User,
    db: Session,
) -> LessonReport:
    """
    報告書を理由付きでクローズする。レコードは削除しない。
    """
    report = db.get(LessonReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="report not found")

    if report.status == ReportStatus.closed.value:
        return report

    if not close_reason or not close_reason.strip():
        raise HTTPException(status_code=422, detail="close_reason is required")

    report.status = ReportStatus.closed.value
    report.closed_at = get_current_jst()
    report.closed_by = closed_by_user.id
    report.close_reason = close_reason.strip()
    db.commit()
    db.refresh(report)
    report.status = ReportStatus.closed.value
    return report


def set_stale_since(reports: list[LessonReport], db: Session) -> None:
    """
    stale_since が未セットの未処理報告書に現在日時をセットする。
    すでにセット済みの場合は上書きしない。
    """
    now = get_current_jst()
    for report in reports:
        if report.stale_since is None:
            report.stale_since = now
    db.commit()
