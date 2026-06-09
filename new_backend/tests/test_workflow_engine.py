"""
ワークフローエンジンのユニットテスト。
DBは使わず definitions / engine の純粋なロジックを検証する。
"""
import uuid

import pytest

from app.workflow.definitions import (
    WorkAction,
    WorkStatus,
    find_transition,
)
from app.workflow.engine import apply_transition
from app.workflow.exceptions import CommentRequired, InvalidTransition, PermissionDenied


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

class _FakeUser:
    def __init__(self, role: str):
        self.id = uuid.uuid4()
        self.role = role


class _FakeReport:
    def __init__(self, status: str):
        self.id = uuid.uuid4()
        self.status = status
        self.current_approver_role = None
        self.submitted_at = None
        self.updated_at = None

    # apply_transition が WorkReportEvent を db.add() するためのスタブ
    class _FakeDB:
        def add(self, obj): pass

    _db = _FakeDB()


def _apply(report_status: str, action: str, actor_role: str, comment: str | None = None):
    """apply_transition を呼び出して (from, to) タプルを返す。"""
    user = _FakeUser(actor_role)
    report = _FakeReport(report_status)
    db = _FakeReport._db

    # WorkReportEvent の生成は DB に依存するのでモンキーパッチ
    import app.workflow.engine as eng_mod
    original = eng_mod.WorkReportEvent

    captured = []

    class _CaptureEvent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    eng_mod.WorkReportEvent = _CaptureEvent
    try:
        apply_transition(db, report, user, action, actor_role, comment)
    finally:
        eng_mod.WorkReportEvent = original

    return report.status


# ---------------------------------------------------------------------------
# definitions: find_transition
# ---------------------------------------------------------------------------

class TestFindTransition:
    def test_tutor_can_submit_draft(self):
        t = find_transition(WorkStatus.DRAFT, WorkAction.SUBMIT, "tutor")
        assert t is not None
        assert t.to_status == WorkStatus.AWAITING_SCHOOL

    def test_school_can_approve_awaiting_school(self):
        t = find_transition(WorkStatus.AWAITING_SCHOOL, WorkAction.APPROVE, "school")
        assert t is not None
        assert t.to_status == WorkStatus.AWAITING_OFFICE

    def test_wrong_role_returns_none(self):
        assert find_transition(WorkStatus.AWAITING_SCHOOL, WorkAction.APPROVE, "tutor") is None

    def test_invalid_action_returns_none(self):
        assert find_transition(WorkStatus.DRAFT, "nonexistent", "tutor") is None

    def test_skip_school_allowed_only_for_admin_chief(self):
        # 学校承認スキップは管理責任者(admin_chief)のみ実行可能
        t = find_transition(WorkStatus.DRAFT, WorkAction.SKIP_SCHOOL, "admin_chief")
        assert t is not None, "skip_school should be allowed for admin_chief"
        assert t.to_status == WorkStatus.AWAITING_OFFICE

    def test_skip_school_not_allowed_for_other_roles(self):
        # 経理(admin_master)・営業・事務・講師はスキップ不可
        for role in ("sales", "office", "admin_master", "tutor", "school"):
            assert find_transition(WorkStatus.DRAFT, WorkAction.SKIP_SCHOOL, role) is None, \
                f"skip_school should NOT be allowed for {role}"

    def test_return_requires_comment(self):
        for from_status, role in [
            (WorkStatus.AWAITING_SCHOOL, "school"),
            (WorkStatus.AWAITING_OFFICE, "office"),
            (WorkStatus.AWAITING_SALES, "sales"),
            (WorkStatus.APPROVED, "sales"),
        ]:
            t = find_transition(from_status, WorkAction.RETURN, role)
            assert t is not None
            assert t.comment_required is True

    def test_sales_return_goes_to_returned_to_office(self):
        t = find_transition(WorkStatus.AWAITING_SALES, WorkAction.RETURN, "sales")
        assert t.to_status == WorkStatus.RETURNED_TO_OFFICE

    def test_returned_to_office_resubmit_by_office(self):
        t = find_transition(WorkStatus.RETURNED_TO_OFFICE, WorkAction.SUBMIT, "office")
        assert t is not None
        assert t.to_status == WorkStatus.AWAITING_SALES

    def test_sales_can_return_approved(self):
        # 営業が最終承認者。完了後の差戻しも営業が行う
        t = find_transition(WorkStatus.APPROVED, WorkAction.RETURN, "sales")
        assert t is not None
        assert t.to_status == WorkStatus.RETURNED_TO_OFFICE
        assert t.comment_required is True

    def test_non_sales_cannot_return_approved(self):
        for role in ("school", "office", "tutor", "admin_master", "admin_chief"):
            assert find_transition(WorkStatus.APPROVED, WorkAction.RETURN, role) is None

    def test_sales_approve_is_final(self):
        # 営業承認で完了（経理ステップ廃止）
        t = find_transition(WorkStatus.AWAITING_SALES, WorkAction.APPROVE, "sales")
        assert t is not None
        assert t.to_status == WorkStatus.APPROVED

    def test_no_finance_approval_step(self):
        # 経理(admin_master/admin_chief)の承認遷移は存在しない
        for role in ("admin_master", "admin_chief"):
            assert find_transition(WorkStatus.AWAITING_FINANCE, WorkAction.APPROVE, role) is None

    def test_office_approves_returned_to_office_forward(self):
        # 営業/経理から事務へ差し戻された報告を事務が承認＝営業確認待ちへ前進
        t = find_transition(WorkStatus.RETURNED_TO_OFFICE, WorkAction.APPROVE, "office")
        assert t is not None
        assert t.to_status == WorkStatus.AWAITING_SALES

    def test_office_returns_returned_to_office_to_tutor(self):
        # 事務がさらに講師へ差し戻す
        t = find_transition(WorkStatus.RETURNED_TO_OFFICE, WorkAction.RETURN, "office")
        assert t is not None
        assert t.to_status == WorkStatus.RETURNED_TO_TUTOR
        assert t.comment_required is True


# ---------------------------------------------------------------------------
# engine: apply_transition
# ---------------------------------------------------------------------------

class TestApplyTransition:
    def test_happy_path_draft_to_awaiting_school(self):
        to = _apply(WorkStatus.DRAFT, WorkAction.SUBMIT, "tutor")
        assert to == WorkStatus.AWAITING_SCHOOL

    def test_full_approval_chain(self):
        steps = [
            (WorkStatus.DRAFT, WorkAction.SUBMIT, "tutor", WorkStatus.AWAITING_SCHOOL),
            (WorkStatus.AWAITING_SCHOOL, WorkAction.APPROVE, "school", WorkStatus.AWAITING_OFFICE),
            (WorkStatus.AWAITING_OFFICE, WorkAction.APPROVE, "office", WorkStatus.AWAITING_SALES),
            (WorkStatus.AWAITING_SALES, WorkAction.APPROVE, "sales", WorkStatus.APPROVED),
        ]
        for from_s, action, role, expected in steps:
            assert _apply(from_s, action, role) == expected

    def test_permission_denied_raises(self):
        with pytest.raises(PermissionDenied):
            _apply(WorkStatus.AWAITING_SCHOOL, WorkAction.APPROVE, "tutor")

    def test_invalid_transition_raises(self):
        with pytest.raises(InvalidTransition):
            _apply(WorkStatus.DRAFT, WorkAction.APPROVE, "school")

    def test_return_without_comment_raises(self):
        with pytest.raises(CommentRequired):
            _apply(WorkStatus.AWAITING_SCHOOL, WorkAction.RETURN, "school", comment=None)

    def test_return_with_empty_comment_raises(self):
        with pytest.raises(CommentRequired):
            _apply(WorkStatus.AWAITING_SCHOOL, WorkAction.RETURN, "school", comment="   ")

    def test_return_with_comment_succeeds(self):
        to = _apply(WorkStatus.AWAITING_SCHOOL, WorkAction.RETURN, "school", comment="修正してください")
        assert to == WorkStatus.RETURNED_TO_TUTOR

    def test_skip_school_then_return_from_sales_goes_to_returned_to_office(self):
        # スキップ後のフロー確認
        to = _apply(WorkStatus.AWAITING_SALES, WorkAction.RETURN, "sales", comment="要修正")
        assert to == WorkStatus.RETURNED_TO_OFFICE

    def test_returned_to_office_resubmit_goes_to_awaiting_sales(self):
        to = _apply(WorkStatus.RETURNED_TO_OFFICE, WorkAction.SUBMIT, "office")
        assert to == WorkStatus.AWAITING_SALES

    def test_403_when_valid_action_wrong_role(self):
        """同じアクションでも別のロールはPermissionDenied"""
        with pytest.raises(PermissionDenied):
            _apply(WorkStatus.AWAITING_SALES, WorkAction.APPROVE, "office")

    def test_422_when_action_not_possible_from_status(self):
        """ステータス的に存在しないアクションはInvalidTransition"""
        with pytest.raises(InvalidTransition):
            _apply(WorkStatus.APPROVED, WorkAction.SUBMIT, "tutor")
