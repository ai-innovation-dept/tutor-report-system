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
def users(db):
    tutor = User(email="step4-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師A", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    other_tutor = User(email="step4-other@example.com", role="tutor", roles=["tutor"], display_name="講師B", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="step4-school@example.com", role="school", roles=["school"], display_name="学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    sales = User(email="step4-sales@example.com", role="sales", roles=["sales"], display_name="営業", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, other_tutor, school, sales])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="Step4生徒A")
    other_assignment = Assignment(tutor_id=other_tutor.id, parent_id=school.id, student_name="Step4生徒B")
    db.add_all([assignment, other_assignment])
    db.commit()
    return {
        "tutor": tutor,
        "other_tutor": other_tutor,
        "school": school,
        "sales": sales,
        "assignment": assignment,
        "other_assignment": other_assignment,
    }


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _report(db, assignment, tutor, target_month="2026-06", status=WorkStatus.APPROVED):
    report = WorkReport(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        target_month=target_month,
        form_type="monthly_dispatch",
        form_data={
            "lines": [
                {
                    "date": "2026-06-01",
                    "start": "09:00",
                    "end": "10:00",
                    "subject_period": 1,
                    "teach_minutes": 60,
                    "break_minutes": 0,
                    "commute_fee": 500,
                    "note": "数学",
                }
            ]
        },
        status=status,
        current_approver_role=None,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def test_tutor_can_export_own_approved_report(client, db, users):
    _report(db, users["assignment"], users["tutor"])

    res = client.get(
        f"/api/w/reports/export?target_month=2026-06&assignment_id={users['assignment'].id}",
        headers=_auth(client, "step4-tutor@example.com"),
    )

    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_tutor_cannot_export_other_tutor_report(client, db, users):
    _report(db, users["other_assignment"], users["other_tutor"])

    res = client.get(
        f"/api/w/reports/export?target_month=2026-06&assignment_id={users['other_assignment'].id}",
        headers=_auth(client, "step4-tutor@example.com"),
    )

    assert res.status_code == 403


def test_sales_can_export_all_reports(client, db, users):
    _report(db, users["assignment"], users["tutor"])
    _report(db, users["other_assignment"], users["other_tutor"])

    res = client.get(
        "/api/w/reports/export?target_month=2026-06&scope=all",
        headers=_auth(client, "step4-sales@example.com"),
    )

    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_export_returns_404_when_no_reports(client, users):
    res = client.get(
        "/api/w/reports/export?target_month=2026-06&scope=all",
        headers=_auth(client, "step4-sales@example.com"),
    )

    assert res.status_code == 404
