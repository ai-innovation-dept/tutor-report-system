from datetime import datetime, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkReportEvent
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession


def _previous_month() -> str:
    now = datetime.now(timezone.utc)
    year = now.year
    month = now.month - 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year}-{month:02d}"


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def users(db):
    tutor = User(email="b-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="b-school@example.com", role="school", roles=["school"], display_name="学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    sales = User(email="b-sales@example.com", role="sales", roles=["sales"], display_name="営業", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    office = User(email="b-office@example.com", role="office", roles=["office"], display_name="事務", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    master = User(email="b-master@example.com", role="admin_master", roles=["admin_master"], display_name="管理者", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school, sales, office, master])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="Step2B生徒")
    db.add(assignment)
    db.commit()
    return {"tutor": tutor, "school": school, "sales": sales, "office": office, "master": master, "assignment": assignment}


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_report(client, users, month=None):
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": str(users["assignment"].id),
            "target_month": month or _previous_month(),
            "form_type": "monthly_dispatch",
            "form_data": {"lines": []},
        },
        headers=_auth(client, "b-tutor@example.com"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _advance_to_sales(client, report_id):
    for email, action in [
        ("b-tutor@example.com", "submit"),
        ("b-school@example.com", "approve"),
        ("b-office@example.com", "approve"),
    ]:
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": action}, headers=_auth(client, email))
        assert res.status_code == 200, res.text


def test_action_close_records_close_event(client, db, users):
    report_id = _create_report(client, users)
    res = client.post(
        f"/api/w/reports/{report_id}/action",
        json={"action": "close", "comment": "期限切れ", "actor_role": "sales"},
        headers=_auth(client, "b-sales@example.com"),
    )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == WorkStatus.CLOSED
    assert data["close_reason"] == "期限切れ"

    events = db.query(WorkReportEvent).filter(WorkReportEvent.report_id == uuid.UUID(report_id)).all()
    assert len(events) == 1
    assert events[0].action == "close"
    assert events[0].to_status == WorkStatus.CLOSED


def test_close_endpoint_rejects_current_month(client, users):
    current_month = datetime.now(timezone.utc).strftime("%Y-%m")
    report_id = _create_report(client, users, month=current_month)

    res = client.post(
        f"/api/w/reports/{report_id}/close",
        json={"close_reason": "期限切れ"},
        headers=_auth(client, "b-sales@example.com"),
    )

    assert res.status_code == 422


def test_bulk_action_processes_valid_reports_and_skips_invalid(client, users):
    valid_id = _create_report(client, users)
    invalid_id = _create_report(client, users, month=f"{_previous_month()[:-2]}01")
    _advance_to_sales(client, valid_id)

    res = client.post(
        "/api/w/reports/bulk-action",
        json={"report_ids": [valid_id, invalid_id], "action": "approve", "actor_role": "sales"},
        headers=_auth(client, "b-sales@example.com"),
    )

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["processed"] == 1
    assert data["skipped"] == 1
    assert data["skip_ids"] == [invalid_id]

    assert client.get(f"/api/w/reports/{valid_id}", headers=_auth(client, "b-sales@example.com")).json()["status"] == WorkStatus.AWAITING_FINANCE
    assert client.get(f"/api/w/reports/{invalid_id}", headers=_auth(client, "b-sales@example.com")).json()["status"] == WorkStatus.DRAFT


def test_stale_count_and_stale_reports(client, users):
    report_id = _create_report(client, users)

    tutor_count = client.get("/api/w/stale-count", headers=_auth(client, "b-tutor@example.com"))
    school_count = client.get("/api/w/stale-count", headers=_auth(client, "b-school@example.com"))
    sales_count = client.get("/api/w/stale-count", headers=_auth(client, "b-sales@example.com"))
    stale_reports = client.get("/api/w/stale-reports", headers=_auth(client, "b-sales@example.com"))

    assert tutor_count.status_code == 200
    assert tutor_count.json()["count"] == 1
    assert school_count.json()["count"] == 1
    assert sales_count.json()["count"] == 1
    assert stale_reports.status_code == 200
    assert [report["id"] for report in stale_reports.json()] == [report_id]


def test_monthly_summary_includes_new_fields(client, users):
    report_id = _create_report(client, users)

    res = client.get(
        f"/api/w/reports/monthly-summary?target_month={_previous_month()}",
        headers=_auth(client, "b-tutor@example.com"),
    )

    assert res.status_code == 200
    data = res.json()
    assert data["by_status"] == {WorkStatus.DRAFT: 1}
    assert data["pending_action"] is True
    assert data["status_counts"] == data["by_status"]
    assert data["target_month"] == _previous_month()
