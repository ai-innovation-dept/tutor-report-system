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
        password_hash=hash_password("Passw0rd!"),
    )
    school = User(
        email="school@work.example.com",
        role="school",
        roles=["school"],
        display_name="学校担当",
        password_hash=hash_password("Passw0rd!"),
    )
    sales = User(
        email="sales@work.example.com",
        role="sales",
        roles=["sales"],
        display_name="営業担当",
        password_hash=hash_password("Passw0rd!"),
    )
    office = User(
        email="office@work.example.com",
        role="office",
        roles=["office"],
        display_name="事務担当",
        password_hash=hash_password("Passw0rd!"),
    )
    master = User(
        email="master@work.example.com",
        role="admin_master",
        roles=["admin_master"],
        display_name="管理者",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add_all([tutor, school, sales, office, master])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, student_name="テスト生徒")
    db.add(assignment)
    db.commit()
    return {"tutor": tutor, "school": school, "sales": sales, "office": office, "master": master, "assignment": assignment}


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
            ("sales@work.example.com", "approve"),
            ("office@work.example.com", "approve"),
            ("master@work.example.com", "approve"),
        ]
        expected_statuses = [
            WorkStatus.AWAITING_SCHOOL,
            WorkStatus.AWAITING_SALES,
            WorkStatus.AWAITING_OFFICE,
            WorkStatus.AWAITING_FINANCE,
            WorkStatus.APPROVED,
        ]
        for (email, action), expected in zip(steps, expected_statuses):
            res = client.post(f"/api/w/reports/{report_id}/action",
                              json={"action": action},
                              headers=_auth(client, email))
            assert res.status_code == 200, f"{email}/{action}: {res.text}"
            assert res.json()["status"] == expected

    def test_skip_school_by_sales(self, client, users):
        report_id = self._create_report(client, users)
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "skip_school"},
                          headers=_auth(client, "sales@work.example.com"))
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_SALES

    def test_return_from_office_goes_to_returned_to_sales(self, client, users):
        report_id = self._create_report(client, users)
        for email, action in [
            ("tutor@work.example.com", "submit"),
            ("school@work.example.com", "approve"),
            ("sales@work.example.com", "approve"),
        ]:
            client.post(f"/api/w/reports/{report_id}/action", json={"action": action},
                        headers=_auth(client, email))
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "return", "comment": "要修正"},
                          headers=_auth(client, "office@work.example.com"))
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.RETURNED_TO_SALES

    def test_returned_to_sales_resubmit_by_sales_goes_to_awaiting_office(self, client, users):
        report_id = self._create_report(client, users)
        for email, action, comment in [
            ("tutor@work.example.com", "submit", None),
            ("school@work.example.com", "approve", None),
            ("sales@work.example.com", "approve", None),
            ("office@work.example.com", "return", "要修正"),
        ]:
            client.post(f"/api/w/reports/{report_id}/action",
                        json={"action": action, "comment": comment},
                        headers=_auth(client, email))
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "submit"},
                          headers=_auth(client, "sales@work.example.com"))
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE
