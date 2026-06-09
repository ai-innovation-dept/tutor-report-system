"""
報告書 API の統合テスト（SQLite インメモリ）。
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def users(db):
    tutor = User(
        email="tutor@work.example.com",
        role="tutor",
        roles=["tutor"],
        display_name="講師A",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )
    school = User(
        email="school@work.example.com",
        role="school",
        roles=["school"],
        display_name="学校担当",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )
    sales = User(
        email="sales@work.example.com",
        role="sales",
        roles=["sales"],
        display_name="営業担当",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )
    office = User(
        email="office@work.example.com",
        role="office",
        roles=["office"],
        display_name="事務担当",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )
    master = User(
        email="master@work.example.com",
        role="admin_master",
        roles=["admin_master"],
        display_name="管理者",
        allowed_systems=["legacy", "new"],
        password_hash=hash_password("Passw0rd!"),
    )
    chief = User(
        email="chief@work.example.com",
        role="admin_chief",
        roles=["admin_chief"],
        display_name="管理責任者",
        allowed_systems=["legacy", "new"],
        password_hash=hash_password("Passw0rd!"),
    )
    # 事務・営業を兼務するスタッフ（職務分掌の対象）
    dual = User(
        email="dual@work.example.com",
        role="office",
        roles=["office", "sales"],
        display_name="事務営業兼務",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )
    tutor2 = User(
        email="tutor2@work.example.com",
        role="tutor",
        roles=["tutor"],
        display_name="講師B",
        allowed_systems=["new"],
        password_hash=hash_password("Passw0rd!"),
    )
    db.add_all([tutor, school, sales, office, master, chief, dual, tutor2])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, student_name="テスト生徒", system_type="new")
    assignment2 = Assignment(tutor_id=tutor2.id, student_name="テスト生徒B", system_type="new")
    db.add_all([assignment, assignment2])
    db.commit()
    return {
        "tutor": tutor, "school": school, "sales": sales, "office": office,
        "master": master, "chief": chief, "dual": dual, "tutor2": tutor2,
        "assignment": assignment, "assignment2": assignment2,
    }


@pytest.fixture()
def client():
    return TestClient(app)


def _token(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return res.json()["access_token"]


def _auth(client, email):
    return {"Authorization": f"Bearer {_token(client, email)}"}


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------

class TestAuth:
    def test_login_tutor_ok(self, client, users):
        res = client.post("/api/auth/login", json={"username": "tutor@work.example.com", "password": "Passw0rd!"})
        assert res.status_code == 200
        assert res.json()["role"] == "tutor"

    def test_login_wrong_password(self, client, users):
        res = client.post("/api/auth/login", json={"username": "tutor@work.example.com", "password": "wrong"})
        assert res.status_code == 401

    def test_me(self, client, users):
        res = client.get("/api/auth/me", headers=_auth(client, "tutor@work.example.com"))
        assert res.status_code == 200
        assert res.json()["role"] == "tutor"


# ---------------------------------------------------------------------------
# 報告書 CRUD
# ---------------------------------------------------------------------------

class TestReports:
    def test_tutor_can_create_report(self, client, users):
        headers = _auth(client, "tutor@work.example.com")
        payload = {
            "assignment_id": str(users["assignment"].id),
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {"lines": []},
        }
        res = client.post("/api/w/reports", json=payload, headers=headers)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["status"] == WorkStatus.DRAFT
        assert data["target_month"] == "2026-06"

    def test_duplicate_assignment_month_returns_409(self, client, users):
        headers = _auth(client, "tutor@work.example.com")
        payload = {
            "assignment_id": str(users["assignment"].id),
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {},
        }
        res1 = client.post("/api/w/reports", json=payload, headers=headers)
        assert res1.status_code == 201
        res2 = client.post("/api/w/reports", json=payload, headers=headers)
        assert res2.status_code == 409

    def test_school_cannot_create_report(self, client, users):
        headers = _auth(client, "school@work.example.com")
        payload = {
            "assignment_id": str(users["assignment"].id),
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {},
        }
        res = client.post("/api/w/reports", json=payload, headers=headers)
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# ワークフロー
# ---------------------------------------------------------------------------

class TestWorkflow:
    def _create_report(self, client, users, target_month="2026-06"):
        headers = _auth(client, "tutor@work.example.com")
        payload = {
            "assignment_id": str(users["assignment"].id),
            "target_month": target_month,
            "form_type": "monthly_dispatch",
            "form_data": {"lines": []},
        }
        res = client.post("/api/w/reports", json=payload, headers=headers)
        assert res.status_code == 201
        return res.json()["id"]

    def test_tutor_submits_to_awaiting_school(self, client, users):
        report_id = self._create_report(client, users)
        headers = _auth(client, "tutor@work.example.com")
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_wrong_role_returns_403(self, client, users):
        report_id = self._create_report(client, users)
        # tutor submits first
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"},
                    headers=_auth(client, "tutor@work.example.com"))
        # tutor tries to approve (should be school)
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"},
                          headers=_auth(client, "tutor@work.example.com"))
        assert res.status_code == 403

    def test_return_without_comment_returns_422(self, client, users):
        report_id = self._create_report(client, users)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"},
                    headers=_auth(client, "tutor@work.example.com"))
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "return", "comment": ""},
                          headers=_auth(client, "school@work.example.com"))
        assert res.status_code == 422

    def test_full_approval_chain(self, client, users):
        report_id = self._create_report(client, users)
        steps = [
            ("tutor@work.example.com", "submit"),
            ("school@work.example.com", "approve"),
            ("office@work.example.com", "approve"),
            ("sales@work.example.com", "approve"),
            ("master@work.example.com", "approve"),
        ]
        expected_statuses = [
            WorkStatus.AWAITING_SCHOOL,
            WorkStatus.AWAITING_OFFICE,
            WorkStatus.AWAITING_SALES,
            WorkStatus.AWAITING_FINANCE,
            WorkStatus.APPROVED,
        ]
        for (email, action), expected in zip(steps, expected_statuses):
            res = client.post(f"/api/w/reports/{report_id}/action",
                              json={"action": action},
                              headers=_auth(client, email))
            assert res.status_code == 200, f"{email}/{action}: {res.text}"
            assert res.json()["status"] == expected

    def test_skip_school_by_admin_chief(self, client, users):
        # 学校承認スキップは管理責任者(admin_chief)のみ実行可能
        report_id = self._create_report(client, users)
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "skip_school"},
                          headers=_auth(client, "chief@work.example.com"))
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE

    def test_skip_school_denied_for_non_chief(self, client, users):
        # 経理(admin_master)・営業・事務はスキップ不可
        cases = [
            ("sales@work.example.com", "2026-01"),
            ("office@work.example.com", "2026-02"),
            ("master@work.example.com", "2026-03"),
        ]
        for email, month in cases:
            report_id = self._create_report(client, users, target_month=month)
            res = client.post(f"/api/w/reports/{report_id}/action",
                              json={"action": "skip_school"},
                              headers=_auth(client, email))
            assert res.status_code == 403, f"{email}: {res.text}"

    def test_return_from_sales_goes_to_returned_to_office(self, client, users):
        report_id = self._create_report(client, users)
        for email, action in [
            ("tutor@work.example.com", "submit"),
            ("school@work.example.com", "approve"),
            ("office@work.example.com", "approve"),
        ]:
            client.post(f"/api/w/reports/{report_id}/action", json={"action": action},
                        headers=_auth(client, email))
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "return", "comment": "要修正"},
                          headers=_auth(client, "sales@work.example.com"))
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.RETURNED_TO_OFFICE

    def test_returned_to_office_resubmit_by_office_goes_to_awaiting_sales(self, client, users):
        report_id = self._create_report(client, users)
        for email, action, comment in [
            ("tutor@work.example.com", "submit", None),
            ("school@work.example.com", "approve", None),
            ("office@work.example.com", "approve", None),
            ("sales@work.example.com", "return", "要修正"),
        ]:
            client.post(f"/api/w/reports/{report_id}/action",
                        json={"action": action, "comment": comment},
                        headers=_auth(client, email))
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "submit"},
                          headers=_auth(client, "office@work.example.com"))
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_SALES


# ---------------------------------------------------------------------------
# 事務担当による報告書修正（office-edit）
# ---------------------------------------------------------------------------

class TestOfficeEdit:
    def _advance_to_awaiting_office(self, client, users, target_month="2026-06"):
        headers = _auth(client, "tutor@work.example.com")
        payload = {
            "assignment_id": str(users["assignment"].id),
            "target_month": target_month,
            "form_type": "monthly_dispatch",
            "form_data": {"lines": [{"date": "2026-06-01", "teach_minutes": 60, "note": "数学"}]},
        }
        report_id = client.post("/api/w/reports", json=payload, headers=headers).json()["id"]
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"},
                    headers=_auth(client, "tutor@work.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"},
                    headers=_auth(client, "school@work.example.com"))
        return report_id

    def test_office_can_edit_at_awaiting_office(self, client, users):
        report_id = self._advance_to_awaiting_office(client, users)
        res = client.patch(
            f"/api/w/reports/{report_id}/office-edit",
            json={"form_data": {"lines": [{"date": "2026-06-01", "teach_minutes": 90, "note": "数学(修正)"}]},
                  "comment": "指導時間を修正"},
            headers=_auth(client, "office@work.example.com"),
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["form_data"]["lines"][0]["teach_minutes"] == 90
        # ステータスは変わらない（再承認不要）
        assert data["status"] == WorkStatus.AWAITING_OFFICE
        # 監査イベントが記録される
        assert any(e["action"] == "office_edit" for e in data["events"])

    def test_office_edit_records_comment_event(self, client, users):
        report_id = self._advance_to_awaiting_office(client, users)
        res = client.patch(
            f"/api/w/reports/{report_id}/office-edit",
            json={"form_data": {"lines": []}, "comment": "全削除"},
            headers=_auth(client, "office@work.example.com"),
        )
        assert res.status_code == 200
        edit_events = [e for e in res.json()["events"] if e["action"] == "office_edit"]
        assert edit_events and edit_events[-1]["comment"] == "全削除"

    def test_tutor_cannot_office_edit(self, client, users):
        report_id = self._advance_to_awaiting_office(client, users)
        res = client.patch(
            f"/api/w/reports/{report_id}/office-edit",
            json={"form_data": {"lines": []}},
            headers=_auth(client, "tutor@work.example.com"),
        )
        assert res.status_code == 403

    def test_office_edit_blocked_on_awaiting_finance(self, client, users):
        # 最終確認待ち（経理）まで進めると事務は修正できない
        report_id = self._advance_to_awaiting_office(client, users)
        for email in ("office@work.example.com", "sales@work.example.com"):
            client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"},
                        headers=_auth(client, email))
        res = client.patch(
            f"/api/w/reports/{report_id}/office-edit",
            json={"form_data": {"lines": []}},
            headers=_auth(client, "office@work.example.com"),
        )
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# リマインド設定（運営スタッフ全ロール）
# ---------------------------------------------------------------------------

class TestReminderPermissions:
    def _assignment_id(self, users):
        return str(users["assignment"].id)

    def test_sales_can_set_reminder(self, client, users):
        res = client.patch(
            f"/api/w/assignments/{self._assignment_id(users)}",
            json={"reminder_enabled": True, "reminder_days_after": 5},
            headers=_auth(client, "sales@work.example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["reminder_enabled"] is True
        assert res.json()["reminder_days_after"] == 5

    def test_office_can_set_reminder(self, client, users):
        res = client.patch(
            f"/api/w/assignments/{self._assignment_id(users)}",
            json={"reminder_enabled": True, "reminder_days_after": 7},
            headers=_auth(client, "office@work.example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["reminder_days_after"] == 7

    def test_master_can_set_reminder(self, client, users):
        res = client.patch(
            f"/api/w/assignments/{self._assignment_id(users)}",
            json={"reminder_enabled": True, "reminder_days_after": 3},
            headers=_auth(client, "master@work.example.com"),
        )
        assert res.status_code == 200, res.text

    def test_ops_cannot_change_student_name(self, client, users):
        # 運営スタッフはリマインド項目のみ。student_name 等は無視される（変更されない）
        original = users["assignment"].student_name
        res = client.patch(
            f"/api/w/assignments/{self._assignment_id(users)}",
            json={"student_name": "改ざん", "reminder_enabled": True},
            headers=_auth(client, "sales@work.example.com"),
        )
        assert res.status_code == 200
        assert res.json()["student_name"] == original
        assert res.json()["reminder_enabled"] is True

    def test_school_cannot_set_reminder(self, client, users):
        res = client.patch(
            f"/api/w/assignments/{self._assignment_id(users)}",
            json={"reminder_enabled": True},
            headers=_auth(client, "school@work.example.com"),
        )
        assert res.status_code == 403


# ---------------------------------------------------------------------------
# 職務分掌（事務・営業兼務スタッフは同一講師で兼務不可）
# ---------------------------------------------------------------------------

class TestDutySeparation:
    def _act(self, client, email, report_id, action, actor_role=None, comment=None):
        body = {"action": action}
        if actor_role:
            body["actor_role"] = actor_role
        if comment:
            body["comment"] = comment
        return client.post(f"/api/w/reports/{report_id}/action", json=body, headers=_auth(client, email))

    def _to_awaiting_office(self, client, users, assignment, tutor_email, month):
        rid = client.post(
            "/api/w/reports",
            json={"assignment_id": str(assignment.id), "target_month": month,
                  "form_type": "monthly_dispatch", "form_data": {"lines": []}},
            headers=_auth(client, tutor_email),
        ).json()["id"]
        self._act(client, tutor_email, rid, "submit")
        self._act(client, "school@work.example.com", rid, "approve", "school")
        return rid

    def _to_awaiting_sales(self, client, users, assignment, tutor_email, month, office_email="office@work.example.com"):
        rid = self._to_awaiting_office(client, users, assignment, tutor_email, month)
        self._act(client, office_email, rid, "approve", "office")
        return rid

    def test_dual_cannot_sales_after_office_same_tutor(self, client, users):
        # 兼務スタッフが講師Aを事務承認 → 同じ講師Aの別報告を営業承認しようとすると拒否（講師単位）
        r1 = self._to_awaiting_office(client, users, users["assignment"], "tutor@work.example.com", "2026-06")
        assert self._act(client, "dual@work.example.com", r1, "approve", "office").status_code == 200
        r2 = self._to_awaiting_sales(client, users, users["assignment"], "tutor@work.example.com", "2026-07")
        res = self._act(client, "dual@work.example.com", r2, "approve", "sales")
        assert res.status_code == 403, res.text
        assert "事務" in res.json()["detail"]

    def test_dual_cannot_office_after_sales_same_tutor(self, client, users):
        # 兼務スタッフが講師Bを営業承認 → 同じ講師Bの別報告を事務承認しようとすると拒否
        r1 = self._to_awaiting_sales(client, users, users["assignment2"], "tutor2@work.example.com", "2026-06")
        assert self._act(client, "dual@work.example.com", r1, "approve", "sales").status_code == 200
        r2 = self._to_awaiting_office(client, users, users["assignment2"], "tutor2@work.example.com", "2026-07")
        res = self._act(client, "dual@work.example.com", r2, "approve", "office")
        assert res.status_code == 403, res.text
        assert "営業" in res.json()["detail"]

    def test_dual_can_act_different_tutors(self, client, users):
        # 講師が異なれば事務承認・営業承認の両方を行える
        r1 = self._to_awaiting_office(client, users, users["assignment"], "tutor@work.example.com", "2026-06")
        assert self._act(client, "dual@work.example.com", r1, "approve", "office").status_code == 200
        r2 = self._to_awaiting_sales(client, users, users["assignment2"], "tutor2@work.example.com", "2026-06")
        assert self._act(client, "dual@work.example.com", r2, "approve", "sales").status_code == 200

    def test_separation_locks_endpoint(self, client, users):
        r1 = self._to_awaiting_office(client, users, users["assignment"], "tutor@work.example.com", "2026-06")
        self._act(client, "dual@work.example.com", r1, "approve", "office")
        res = client.get("/api/w/reports/admin-separation-locks", headers=_auth(client, "dual@work.example.com"))
        assert res.status_code == 200, res.text
        data = res.json()
        assert str(users["tutor"].id) in data["office_tutor_ids"]
        assert data["sales_tutor_ids"] == []

    def test_single_role_office_locks_empty(self, client, users):
        # 兼務でない単一ロールのスタッフは職務分掌の対象外（ロックは常に空）
        r1 = self._to_awaiting_office(client, users, users["assignment"], "tutor@work.example.com", "2026-06")
        self._act(client, "office@work.example.com", r1, "approve", "office")
        res = client.get("/api/w/reports/admin-separation-locks", headers=_auth(client, "office@work.example.com"))
        assert res.status_code == 200
        assert res.json() == {"office_tutor_ids": [], "sales_tutor_ids": []}

    def test_single_sales_not_blocked_after_other_office(self, client, users):
        # 別人(事務)が事務承認した講師でも、単一ロールの営業は営業承認できる
        r1 = self._to_awaiting_sales(client, users, users["assignment"], "tutor@work.example.com", "2026-06")
        res = self._act(client, "sales@work.example.com", r1, "approve", "sales")
        assert res.status_code == 200, res.text
