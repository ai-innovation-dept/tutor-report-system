"""講師画面改修（下書き報告の削除・差戻し理由の公開）のテスト。"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkReport
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
def client():
    return TestClient(app)


def _add_user(db, email, role):
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(user)
    db.commit()
    return user


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture()
def setup(db):
    tutor = _add_user(db, "tutor@x.example.com", "tutor")
    school = _add_user(db, "school@x.example.com", "school")
    assignment = Assignment(
        tutor_id=tutor.id,
        parent_id=school.id,
        student_name="生徒A",
        system_type="new",
    )
    db.add(assignment)
    db.commit()
    return {"tutor": tutor, "school": school, "assignment": assignment}


def _create_report(client, assignment, headers):
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": str(assignment.id),
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {"lines": []},
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


class TestDeleteDraftReport:
    def test_tutor_can_delete_draft(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)

        res = client.delete(f"/api/w/reports/{report_id}", headers=headers)
        assert res.status_code == 204
        assert db.query(WorkReport).count() == 0

    def test_cannot_delete_after_submit(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers)
        assert res.status_code == 200

        res = client.delete(f"/api/w/reports/{report_id}", headers=headers)
        assert res.status_code == 409

    def test_other_tutor_cannot_delete(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)
        _add_user(db, "tutor2@x.example.com", "tutor")

        res = client.delete(f"/api/w/reports/{report_id}", headers=_auth(client, "tutor2@x.example.com"))
        assert res.status_code == 403


class TestLastReturnComment:
    def test_return_comment_exposed_in_report(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

        school_headers = _auth(client, "school@x.example.com")
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "時間が間違っています"},
            headers=school_headers,
        )
        assert res.status_code == 200, res.text

        res = client.get(f"/api/w/reports/{report_id}", headers=tutor_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == WorkStatus.RETURNED_TO_TUTOR
        assert body["last_return_comment"] == "時間が間違っています"

    def test_no_return_comment_for_draft(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)

        res = client.get(f"/api/w/reports/{report_id}", headers=headers)
        assert res.json()["last_return_comment"] is None
