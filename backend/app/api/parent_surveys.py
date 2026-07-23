# === 保護者アンケート API START（改修 202607231755 ③） ===
"""保護者アンケート（講師への満足度・評価）の回答・閲覧 API。

- 保護者: 自分の子の指導月報に対する自分の回答を取得・作成/更新できる（回答は任意）。
  画面からの送信は承認と同時のみ（改修 202607231908・専用の送信ボタンは廃止）。API はべき等な upsert のまま。
- 運営（受付/再鑑/管理者/管理責任者）: 全回答を一覧取得できる（集計画面 /admin/surveys 用）。
- 講師: 一切アクセス不可（403）。回答内容は PDF・CSV・メール・講師画面のどこにも出さない。
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.rbac import has_role, is_admin
from app.database import get_db
from app.deps import get_current_user
from app.models import MonthlyReport, ParentSurvey, User
from app.schemas import ParentSurveyAdminOut, ParentSurveyIn, ParentSurveyOut

router = APIRouter(prefix="/api/parent-surveys", tags=["parent-surveys"])


def _get_own_monthly(monthly_report_id: UUID, db: Session, user: User) -> MonthlyReport:
    """保護者本人の子の指導月報のみ許可する（他人の月報・存在しないIDは404で区別しない）。"""
    monthly = db.get(MonthlyReport, monthly_report_id)
    if not monthly or monthly.parent_id != user.id:
        raise HTTPException(status_code=404, detail="monthly report not found")
    return monthly


@router.get("/{monthly_report_id}", response_model=ParentSurveyOut | None)
def get_my_survey(
    monthly_report_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保護者本人の回答を返す（未回答なら null）。講師・運営はこのAPIを使わない。"""
    if not has_role(user, "parent"):
        raise HTTPException(status_code=403, detail="parent only")
    _get_own_monthly(monthly_report_id, db, user)
    return db.scalar(select(ParentSurvey).where(ParentSurvey.monthly_report_id == monthly_report_id))


@router.put("/{monthly_report_id}", response_model=ParentSurveyOut)
def upsert_my_survey(
    monthly_report_id: UUID,
    payload: ParentSurveyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保護者本人による回答の作成・更新（指導月報×1件・任意回答のため何度でも更新できる）。"""
    if not has_role(user, "parent"):
        raise HTTPException(status_code=403, detail="parent only")
    monthly = _get_own_monthly(monthly_report_id, db, user)
    comment = (payload.comment or "").strip()[:2000] or None
    survey = db.scalar(select(ParentSurvey).where(ParentSurvey.monthly_report_id == monthly_report_id))
    if survey is None:
        survey = ParentSurvey(
            monthly_report_id=monthly.id,
            assignment_id=monthly.assignment_id,
            tutor_id=monthly.tutor_id,
            parent_id=user.id,
            target_month=monthly.target_month,
        )
        db.add(survey)
    survey.q_satisfaction = payload.q_satisfaction
    survey.q_clarity = payload.q_clarity
    survey.q_communication = payload.q_communication
    survey.q_motivation = payload.q_motivation
    survey.q_punctuality = payload.q_punctuality
    survey.q_continuation = payload.q_continuation
    survey.comment = comment
    db.commit()
    db.refresh(survey)
    return survey


@router.get("", response_model=list[ParentSurveyAdminOut])
def list_surveys(
    tutor_id: UUID | None = None,
    month_from: str | None = None,
    month_to: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """運営スタッフ限定の全回答一覧（/admin/surveys の集計・可視化用）。

    講師は 403（自分への評価も閲覧不可）。保護者も一覧は不可（自分の回答は GET /{id} で参照）。
    絞り込みはクライアント側でも行うため、ここでは任意のプレフィルタのみ提供する。
    """
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="admin only")
    stmt = (
        select(ParentSurvey)
        .options(
            selectinload(ParentSurvey.tutor),
            selectinload(ParentSurvey.parent),
            selectinload(ParentSurvey.assignment),
        )
        .order_by(ParentSurvey.target_month.desc(), ParentSurvey.updated_at.desc())
    )
    if tutor_id:
        stmt = stmt.where(ParentSurvey.tutor_id == tutor_id)
    if month_from:
        stmt = stmt.where(ParentSurvey.target_month >= month_from)
    if month_to:
        stmt = stmt.where(ParentSurvey.target_month <= month_to)
    rows: list[ParentSurveyAdminOut] = []
    for survey in db.scalars(stmt):
        item = ParentSurveyAdminOut.model_validate(survey)
        item.tutor_name = survey.tutor.display_name if survey.tutor else ""
        item.tutor_no = survey.tutor.tutor_no if survey.tutor else None
        item.parent_name = survey.parent.display_name if survey.parent else ""
        item.student_name = survey.assignment.student_name if survey.assignment else ""
        rows.append(item)
    return rows
# === 保護者アンケート API END ===
