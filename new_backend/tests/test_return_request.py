"""
講師起点の差戻し要求（request_return / approve_return_request / decline_return_request）のテスト。

設計（2026-07-10）:
- 講師は提出後（学校・運営側にボールがある間＋最終承認済み）に理由必須で差戻しを要求できる。
- 要求はステータスを変えないイベント。ボールを持つロールが許可すると講師へ差戻り、却下（理由必須）で要求のみ解消。
- 承認等でボールが移った場合、未解決の要求は新しいボール保持ロールへ引き継がれる。
- 解決条件は「許可・却下・講師へ戻る差戻し・クローズ」のみ。メール通知は行わない。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkMailOutbox, WorkNotification, WorkReport, WorkReportEvent
from app.workflow.definitions import WorkAction, WorkStatus
from tests.conftest import TestSession


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


def _user(role, email, roles=None):
    return User(
        email=email,
        role=role,
        roles=roles or [role],
        display_name=f"{role}-{email.split('@')[0]}",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )


@pytest.fixture()
def users(db):
    tutor = _user("tutor", "tutor@rr.example.com")
    tutor2 = _user("tutor", "tutor2@rr.example.com")
    school = _user("school", "school@rr.example.com")
    office = _user("office", "office@rr.example.com")
    sales = _user("sales", "sales@rr.example.com")
    dual = _user("office", "dual@rr.example.com", roles=["office", "sales"])
    db.add_all([tutor, tutor2, school, office, sales, dual])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="要求テスト生徒", system_type="new")
    db.add(assignment)
    db.flush()
    db.add(WorkAssignmentProfile(assignment_id=assignment.id, tutor_id=tutor.id, school_id=school.id, form_type="monthly_dispatch"))
    db.commit()
    return {
        "tutor": tutor, "tutor2": tutor2, "school": school,
        "office": office, "sales": sales, "dual": dual, "assignment": assignment,
    }


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _make_report(db, users, status, target_month="2099-01", approver=None):
    report = WorkReport(
        id=uuid.uuid4(),
        assignment_id=users["assignment"].id,
        tutor_id=users["tutor"].id,
        target_month=target_month,
        form_type="monthly_dispatch",
        form_data={"lines": []},
        status=status,
        current_approver_role=approver,
    )
    db.add(report)
    db.commit()
    return report


def _action(client, headers, report_id, action, comment=None, actor_role=None):
    payload = {"action": action}
    if comment is not None:
        payload["comment"] = comment
    if actor_role is not None:
        payload["actor_role"] = actor_role
    return client.post(f"/api/w/reports/{report_id}/action", json=payload, headers=headers)


def _get(client, headers, report_id):
    res = client.get(f"/api/w/reports/{report_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# 要求（request_return）
# ---------------------------------------------------------------------------

class TestRequestReturn:
    def test_tutor_can_request_from_awaiting_school(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        headers = _auth(client, "tutor@rr.example.com")
        res = _action(client, headers, report.id, "request_return", comment="時間の入力誤りを修正したい")
        assert res.status_code == 200, res.text
        data = res.json()
        # ステータスは変わらずイベントだけ積まれ、要求中フラグが立つ
        assert data["status"] == WorkStatus.AWAITING_SCHOOL
        assert data["current_approver_role"] == "school"
        assert data["return_request_pending"] is True
        assert data["return_request_comment"] == "時間の入力誤りを修正したい"
        events = list(db.scalars(select(WorkReportEvent).where(WorkReportEvent.report_id == report.id)))
        assert [e.action for e in events] == [WorkAction.REQUEST_RETURN]
        assert events[0].from_status == WorkStatus.AWAITING_SCHOOL
        assert events[0].to_status == WorkStatus.AWAITING_SCHOOL

    @pytest.mark.parametrize("status,approver", [
        (WorkStatus.AWAITING_OFFICE_PRECHECK, "office"),
        (WorkStatus.AWAITING_OFFICE, "office"),
        (WorkStatus.AWAITING_SALES, "sales"),
        (WorkStatus.APPROVED, None),
        (WorkStatus.RETURNED_TO_OFFICE, "office"),
    ])
    def test_tutor_can_request_from_all_ball_holder_statuses(self, client, db, users, status, approver):
        report = _make_report(db, users, status, approver=approver)
        headers = _auth(client, "tutor@rr.example.com")
        res = _action(client, headers, report.id, "request_return", comment="修正したい")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == status
        assert res.json()["return_request_pending"] is True

    def test_request_requires_comment(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        headers = _auth(client, "tutor@rr.example.com")
        assert _action(client, headers, report.id, "request_return").status_code == 422
        assert _action(client, headers, report.id, "request_return", comment="  ").status_code == 422

    def test_duplicate_request_conflicts(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        headers = _auth(client, "tutor@rr.example.com")
        assert _action(client, headers, report.id, "request_return", comment="1回目").status_code == 200
        res = _action(client, headers, report.id, "request_return", comment="2回目")
        assert res.status_code == 409

    def test_request_invalid_from_draft_and_returned(self, client, db, users):
        headers = _auth(client, "tutor@rr.example.com")
        for status in (WorkStatus.DRAFT, WorkStatus.RETURNED_TO_TUTOR):
            report = _make_report(db, users, status, target_month=f"2099-0{2 if status == WorkStatus.DRAFT else 3}", approver="tutor")
            res = _action(client, headers, report.id, "request_return", comment="対象外")
            assert res.status_code == 409, status

    def test_other_roles_cannot_request(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        for email in ("school@rr.example.com", "office@rr.example.com"):
            res = _action(client, _auth(client, email), report.id, "request_return", comment="不可")
            assert res.status_code == 403, email

    def test_tutor_cannot_request_others_report(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        res = _action(client, _auth(client, "tutor2@rr.example.com"), report.id, "request_return", comment="他人の報告")
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# 許可（approve_return_request）
# ---------------------------------------------------------------------------

class TestApproveReturnRequest:
    def test_school_approves_request_returns_to_tutor(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="金額修正のため")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "approve_return_request")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == WorkStatus.RETURNED_TO_TUTOR
        assert data["current_approver_role"] == "tutor"
        assert data["return_request_pending"] is False
        # 差戻し理由には要求理由が自動転記され、差戻し元は学校になる
        assert "金額修正のため" in data["last_return_comment"]
        assert data["last_return_actor_role"] == "school"

    def test_approve_without_pending_request_conflicts(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "approve_return_request")
        assert res.status_code == 409

    def test_non_ball_holder_cannot_approve_request(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="修正")
        # 学校確認待ちの要求を事務・営業は許可できない
        for email, role in (("office@rr.example.com", "office"), ("sales@rr.example.com", "sales")):
            res = _action(client, _auth(client, email), report.id, "approve_return_request", actor_role=role)
            assert res.status_code == 403, email

    def test_sales_approves_request_on_approved_report(self, client, db, users):
        report = _make_report(db, users, WorkStatus.APPROVED, approver=None)
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="承認後の修正依頼")
        res = _action(client, _auth(client, "sales@rr.example.com"), report.id, "approve_return_request", comment="修正を許可します")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == WorkStatus.RETURNED_TO_TUTOR
        assert data["last_return_actor_role"] == "sales"
        assert "修正を許可します" in data["last_return_comment"]
        assert "承認後の修正依頼" in data["last_return_comment"]

    def test_resubmit_after_approval_clears_request_state(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        tutor_headers = _auth(client, "tutor@rr.example.com")
        _action(client, tutor_headers, report.id, "request_return", comment="修正したい")
        _action(client, _auth(client, "school@rr.example.com"), report.id, "approve_return_request")
        res = _action(client, tutor_headers, report.id, "submit")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == WorkStatus.AWAITING_SCHOOL
        assert data["return_request_pending"] is False
        assert data["return_request_declined_comment"] is None


# ---------------------------------------------------------------------------
# 却下（decline_return_request）
# ---------------------------------------------------------------------------

class TestDeclineReturnRequest:
    def test_decline_keeps_status_and_clears_pending(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        tutor_headers = _auth(client, "tutor@rr.example.com")
        _action(client, tutor_headers, report.id, "request_return", comment="修正したい")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "decline_return_request", comment="このまま承認処理を進めます")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == WorkStatus.AWAITING_SCHOOL
        assert data["current_approver_role"] == "school"
        assert data["return_request_pending"] is False
        assert data["return_request_declined_comment"] == "このまま承認処理を進めます"
        # 却下後は講師が再要求できる
        res2 = _action(client, tutor_headers, report.id, "request_return", comment="再度お願いします")
        assert res2.status_code == 200
        assert res2.json()["return_request_pending"] is True
        assert res2.json()["return_request_declined_comment"] is None

    def test_decline_requires_comment(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="修正したい")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "decline_return_request")
        assert res.status_code == 422

    def test_decline_without_pending_request_conflicts(self, client, db, users):
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "decline_return_request", comment="対象なし")
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# ボール移動時の要求引き継ぎ
# ---------------------------------------------------------------------------

class TestRequestCarryOver:
    def test_request_survives_school_approval_and_next_holder_can_approve(self, client, db, users):
        """学校確認待ちで要求→学校が（要求に気づかず）承認→要求は事務へ引き継がれ、事務が許可できる。"""
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="引き継ぎ確認")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "approve")
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["status"] == WorkStatus.AWAITING_OFFICE
        # 承認でボールが移っても要求は未解決のまま
        assert data["return_request_pending"] is True
        assert data["return_request_comment"] == "引き継ぎ確認"
        # 学校はもうボールを持たないため許可できない
        res_school = _action(client, _auth(client, "school@rr.example.com"), report.id, "approve_return_request")
        assert res_school.status_code == 403
        # 新しいボール保持ロール（事務）が許可すると講師へ差戻る
        res_office = _action(client, _auth(client, "office@rr.example.com"), report.id, "approve_return_request", actor_role="office")
        assert res_office.status_code == 200, res_office.text
        assert res_office.json()["status"] == WorkStatus.RETURNED_TO_TUTOR
        assert res_office.json()["return_request_pending"] is False

    def test_request_survives_to_final_approval_holder(self, client, db, users):
        """事務確認待ちで要求→事務承認→営業承認（完了）まで進んでも要求は営業へ残る。"""
        report = _make_report(db, users, WorkStatus.AWAITING_OFFICE, approver="office")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="最後まで引き継ぎ")
        assert _action(client, _auth(client, "office@rr.example.com"), report.id, "approve", actor_role="office").status_code == 200
        res = _action(client, _auth(client, "sales@rr.example.com"), report.id, "approve", actor_role="sales")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == WorkStatus.APPROVED
        assert data["return_request_pending"] is True
        # 完了後のボール保持ロール＝営業が許可できる
        res_sales = _action(client, _auth(client, "sales@rr.example.com"), report.id, "approve_return_request", actor_role="sales")
        assert res_sales.status_code == 200, res_sales.text
        assert res_sales.json()["status"] == WorkStatus.RETURNED_TO_TUTOR

    def test_normal_return_to_tutor_resolves_request(self, client, db, users):
        """要求中に学校が通常の差戻しをした場合も要求は解決する（講師に報告書が戻るため）。"""
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="修正したい")
        res = _action(client, _auth(client, "school@rr.example.com"), report.id, "return", comment="学校からの通常差戻し")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == WorkStatus.RETURNED_TO_TUTOR
        assert data["return_request_pending"] is False

    def test_close_resolves_request(self, client, db, users):
        """要求中の報告が強制クローズされた場合も要求は解決する。"""
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, target_month="2020-01", approver="school")
        _action(client, _auth(client, "tutor@rr.example.com"), report.id, "request_return", comment="過去月の修正")
        res = client.post(
            f"/api/w/reports/{report.id}/close",
            json={"close_reason": "運用終了のためクローズ"},
            headers=_auth(client, "office@rr.example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.CLOSED
        assert res.json()["return_request_pending"] is False


# ---------------------------------------------------------------------------
# 職務分掌・通知なし・一括要求
# ---------------------------------------------------------------------------

class TestGuardsAndSideEffects:
    def test_duty_separation_blocks_dual_staff_request_approval(self, client, db, users):
        """営業承認を担当済みの兼務スタッフは、同一講師の事務工程で要求を許可できない。"""
        dual_headers = _auth(client, "dual@rr.example.com")
        first = _make_report(db, users, WorkStatus.AWAITING_SALES, target_month="2099-04", approver="sales")
        assert _action(client, dual_headers, first.id, "approve", actor_role="sales").status_code == 200
        second = _make_report(db, users, WorkStatus.AWAITING_OFFICE, target_month="2099-05", approver="office")
        _action(client, _auth(client, "tutor@rr.example.com"), second.id, "request_return", comment="修正したい")
        res = _action(client, dual_headers, second.id, "approve_return_request", actor_role="office")
        assert res.status_code == 403
        assert "兼務" in res.json()["detail"]
        # 専任の事務なら許可できる
        res_office = _action(client, _auth(client, "office@rr.example.com"), second.id, "approve_return_request", actor_role="office")
        assert res_office.status_code == 200

    def test_request_actions_send_no_mail_and_no_notifications(self, client, db, users):
        """要求・許可・却下ではメールも通知レコードも一切作られない（メール通知なしの設計）。"""
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, approver="school")
        tutor_headers = _auth(client, "tutor@rr.example.com")
        school_headers = _auth(client, "school@rr.example.com")
        _action(client, tutor_headers, report.id, "request_return", comment="修正したい")
        _action(client, school_headers, report.id, "decline_return_request", comment="今回は不可")
        _action(client, tutor_headers, report.id, "request_return", comment="再度お願いします")
        _action(client, school_headers, report.id, "approve_return_request")
        db.expire_all()
        notifications = list(db.scalars(select(WorkNotification).where(WorkNotification.report_id == report.id)))
        outbox = list(db.scalars(select(WorkMailOutbox)))
        assert notifications == []
        assert outbox == []

    def test_bulk_request_return(self, client, db, users):
        """承認管理のグループ操作を想定した一括要求（bulk-action）が機能する。"""
        report = _make_report(db, users, WorkStatus.AWAITING_SCHOOL, target_month="2099-06", approver="school")
        draft = _make_report(db, users, WorkStatus.DRAFT, target_month="2099-07", approver="tutor")
        res = client.post(
            "/api/w/reports/bulk-action",
            json={"action": "request_return", "comment": "まとめて修正したい", "report_ids": [str(report.id), str(draft.id)]},
            headers=_auth(client, "tutor@rr.example.com"),
        )
        assert res.status_code == 200, res.text
        data = res.json()
        # 要求できない下書きはスキップされ、対象のみ処理される
        assert data["processed"] == 1
        assert data["skipped"] == 1
        db.expire_all()
        assert db.get(WorkReport, report.id).return_request_pending is True
        assert db.get(WorkReport, draft.id).return_request_pending is False
