"""
ワークフローエンジン。
遷移ルールは definitions.TRANSITIONS のみを参照する。
"""
import calendar
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkReport, WorkReportEvent
from .definitions import WorkAction, WorkStatus, find_transition
from .exceptions import CommentRequired, InvalidTransition, PermissionDenied


def _skips_school_approval(db: Session, report: WorkReport) -> bool:
    """学校ユーザー単位の承認スキップ設定（users.skip_parent_approval）を返す。

    学校 = assignment.parent。スキップ設定は学校ユーザーに紐づく（経理のユーザ管理で設定）。
    """
    assignment = getattr(report, "assignment", None)
    if assignment is None and getattr(report, "assignment_id", None) is not None and hasattr(db, "get"):
        assignment = db.get(Assignment, report.assignment_id)
    if not assignment or not assignment.parent_id:
        return False
    school = getattr(assignment, "parent", None)
    if school is None and hasattr(db, "get"):
        school = db.get(User, assignment.parent_id)
    return bool(school and school.skip_parent_approval)


def _month_bounds(target_month: str) -> tuple[date, date] | None:
    """"YYYY-MM" から月初日・月末日を返す。形式不正は None。"""
    try:
        year, month = (int(part) for part in target_month.split("-", 1))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    except (ValueError, AttributeError):
        return None


def _case_minutes_limit(case: dict, month_start: date, month_end: date) -> tuple[int, int] | None:
    """ケースが対象月に適用されるなら (task_index, 月分上限) を返す。対象外は None。"""
    if not isinstance(case, dict):
        return None
    limit = case.get("monthly_minutes")
    if not isinstance(limit, int):
        return None
    try:
        start = date.fromisoformat(case["start_date"]) if case.get("start_date") else None
        end = date.fromisoformat(case["end_date"]) if case.get("end_date") else None
    except (TypeError, ValueError):
        return None
    if start and start > month_end:
        return None
    if end and end < month_start:
        return None
    return int(case.get("task_index") or 1), limit


def exceeds_monthly_limit(db: Session, report: WorkReport) -> bool:
    """担当業務ごとの対象月の分数合計が、契約の月分固定（紐づくケース）を超えているか。

    判定対象: 担当業務（task_minutes_N）のみ。週コマ・副業務・ケース未登録の業務は対象外。
    ケースは適用期間が対象月と重なるものを使用する（期間未設定は常に適用）。
    1件でも超過していれば True（提出時に承認フローを超過フローへ切り替える）。
    """
    # _skips_school_approval と同様、テスト用スタブでも安全に動くよう防御的に参照する
    assignment_id = getattr(report, "assignment_id", None)
    if assignment_id is None or not hasattr(db, "scalar"):
        return False
    bounds = _month_bounds(getattr(report, "target_month", "") or "")
    if bounds is None:
        return False
    profile = db.scalar(
        select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.assignment_id == assignment_id,
            WorkAssignmentProfile.is_active.is_(True),
        )
    )
    if not profile or not profile.workload_cases:
        return False
    lines = (getattr(report, "form_data", None) or {}).get("lines") or []

    def task_total(task_index: int) -> int:
        key = f"task_minutes_{task_index}"
        total = 0
        for line in lines:
            if not isinstance(line, dict):
                continue
            try:
                total += int(line.get(key, 0) or 0)
            except (TypeError, ValueError):
                continue
        return total

    for case in profile.workload_cases:
        applicable = _case_minutes_limit(case, *bounds)
        if applicable is None:
            continue
        task_index, limit = applicable
        if task_total(task_index) > limit:
            return True
    return False


def apply_transition(
    db: Session,
    report: WorkReport,
    actor: User,
    action: str,
    actor_role: str,
    comment: str | None = None,
) -> WorkReport:
    """
    報告書にアクションを適用し、ステータスを遷移させる。
    成功時: report を更新して WorkReportEvent を追加、commit は呼び出し元の責任。
    失敗時: InvalidTransition / PermissionDenied / CommentRequired を送出。
    """
    transition = find_transition(report.status, action, actor_role)

    if transition is None:
        if not any(
            actor_role in t.allowed_roles
            for t in []
        ):
            pass
        from app.workflow.definitions import _INDEX
        valid_actions = {t.action for key, ts in _INDEX.items() if key[0] == report.status for t in ts}
        role_mismatch = action in valid_actions
        if role_mismatch:
            raise PermissionDenied(
                f"role '{actor_role}' is not allowed to perform '{action}' on status '{report.status}'"
            )
        raise InvalidTransition(
            f"action '{action}' is not valid from status '{report.status}'"
        )

    if transition.comment_required and not (comment and comment.strip()):
        raise CommentRequired("comment is required for this action")

    from_status = report.status
    to_status = transition.to_status
    next_approver_role = transition.next_approver_role

    # 学校スキップ設定が有効な紐付けは、講師提出時に学校確認を飛ばして事務確認へ進める。
    # 月分超過の報告でも学校スキップ校は事務確認1回（通常スキップフローと同形）とする。
    if (
        action == WorkAction.SUBMIT
        and to_status == WorkStatus.AWAITING_SCHOOL
        and _skips_school_approval(db, report)
    ):
        to_status = WorkStatus.AWAITING_OFFICE
        next_approver_role = "office"
    # 担当業務の月分が契約の月分固定を超過した報告は、学校確認の前に事務の事前確認を挟む
    # （超過フロー: 講師→事務→学校→事務→営業）
    elif (
        action == WorkAction.SUBMIT
        and to_status == WorkStatus.AWAITING_SCHOOL
        and exceeds_monthly_limit(db, report)
    ):
        to_status = WorkStatus.AWAITING_OFFICE_PRECHECK
        next_approver_role = "office"

    report.status = to_status
    report.current_approver_role = next_approver_role
    report.updated_at = datetime.now(timezone.utc)

    if action == WorkAction.SUBMIT and from_status in ("draft", "returned_to_tutor", "returned_to_office"):
        report.submitted_at = datetime.now(timezone.utc)

    event = WorkReportEvent(
        report_id=report.id,
        actor_id=actor.id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        comment=comment,
    )
    db.add(event)
    return report
