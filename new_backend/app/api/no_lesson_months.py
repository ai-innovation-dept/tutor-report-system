"""講師の「当月授業なし」申請API（改修依頼 202607161140）。

長期休業などで授業を行わない月を講師本人が申請する（講師×月・全契約対象）。
申請中の講師は、学校の「契約講師全員の学校承認完了」通知（事務・営業宛）の集計対象外になる。
申請によって全員承認が成立した学校があれば、その場で完了メールを投函する。
報告書の作成・提出そのものは制限しない。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dependencies.auth import require_role
from app.models.shared import User
from app.models.work import WorkNoLessonMonth
from app.schemas.no_lesson_months import NoLessonMonthsOut, NoLessonToggleIn, NoLessonToggleOut
from app.schemas.school_settings import MONTH_PATTERN
from app.services.school_progress_service import send_school_all_approved_after_no_lesson

router = APIRouter(prefix="/api/w/no-lesson-months", tags=["work-no-lesson-months"])


def _validate_month(target_month: str) -> str:
    if not MONTH_PATTERN.match(target_month or ""):
        raise HTTPException(status_code=422, detail="対象月は YYYY-MM 形式で指定してください")
    return target_month


@router.get("", response_model=NoLessonMonthsOut)
def list_no_lesson_months(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """自分（講師）が「授業なし」を申請中の月の一覧を返す。"""
    months = db.scalars(
        select(WorkNoLessonMonth.target_month)
        .where(WorkNoLessonMonth.tutor_id == user.id)
        .order_by(WorkNoLessonMonth.target_month)
    ).all()
    return NoLessonMonthsOut(months=list(months))


@router.put("/{target_month}", response_model=NoLessonToggleOut)
def set_no_lesson_month(
    target_month: str,
    payload: NoLessonToggleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("tutor")),
):
    """対象月の「授業なし」申請を設定/解除する（冪等）。

    設定によって「契約講師全員の学校承認」が成立した学校があれば、事務・営業へ完了メールを送る。
    """
    _validate_month(target_month)
    row = db.scalar(
        select(WorkNoLessonMonth).where(
            WorkNoLessonMonth.tutor_id == user.id,
            WorkNoLessonMonth.target_month == target_month,
        )
    )
    if payload.no_lesson and row is None:
        db.add(WorkNoLessonMonth(tutor_id=user.id, target_month=target_month))
        db.commit()
        # 申請で全員承認が成立した学校への完了通知（内部で commit する）
        send_school_all_approved_after_no_lesson(db, user, target_month)
    elif not payload.no_lesson and row is not None:
        db.delete(row)
        db.commit()
    return NoLessonToggleOut(target_month=target_month, no_lesson=payload.no_lesson)
