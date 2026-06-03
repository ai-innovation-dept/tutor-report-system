from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkNotification, WorkReport
from app.services.reminder_service import daily_stale_check, enqueue_month_end_reminders, is_reminder_day
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
    tutor = User(email="step3-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師", password_hash=hash_password("Passw0rd!"))
    other = User(email="step3-other@example.com", role="tutor", roles=["tutor"], display_name="別講師", password_hash=hash_password("Passw0rd!"))
    school = User(email="step3-school@example.com", role="school", roles=["school"], display_name="学校", password_hash=hash_password("Passw0rd!"))
    sales = User(email="step3-sales@example.com", role="sales", roles=["sales"], display_name="営業", password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, other, school, sales])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="Step3生徒")
    db.add(assignment)
    db.commit()
    return {"tutor": tutor, "other": other, "school": school, "sales": sales, "assignment": assignment}


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _previous_month() -> str:
    first = datetime.now(timezone.utc).replace(day=1)
    previous = first - timedelta(days=1)
    return previous.strftime("%Y-%m")


def _create_report(db, users, status=WorkStatus.DRAFT, target_month=None) -> WorkReport:
    report = WorkReport(
        assignment_id=users["assignment"].id,
        tutor_id=users["tutor"].id,
        target_month=target_month or _current_month(),
        form_type="monthly_dispatch",
        form_data={"lines": []},
        status=status,
        current_approver_role="school" if status == WorkStatus.AWAITING_SCHOOL else "tutor",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def test_chat_api_allows_related_users_and_marks_read(client, db, users):
    report = _create_report(db, users)

    tutor_post = client.post(
        f"/api/w/reports/{report.id}/messages",
        json={"body": "講師からの連絡"},
        headers=_auth(client, "step3-tutor@example.com"),
    )
    assert tutor_post.status_code == 201, tutor_post.text

    school_post = client.post(
        f"/api/w/reports/{report.id}/messages",
        json={"body": "学校からの返信"},
        headers=_auth(client, "step3-school@example.com"),
    )
    assert school_post.status_code == 201, school_post.text

    listed = client.get(f"/api/w/reports/{report.id}/messages", headers=_auth(client, "step3-tutor@example.com"))
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    msg_id = listed.json()[0]["id"]
    read = client.post(
        f"/api/w/reports/{report.id}/messages/{msg_id}/read",
        headers=_auth(client, "step3-school@example.com"),
    )
    assert read.status_code == 200
    assert read.json() == {"status": "ok"}


def test_chat_api_rejects_unrelated_user(client, db, users):
    report = _create_report(db, users)

    res = client.post(
        f"/api/w/reports/{report.id}/messages",
        json={"body": "関係なし"},
        headers=_auth(client, "step3-other@example.com"),
    )

    assert res.status_code == 403


def test_is_reminder_day_logic():
    assert is_reminder_day(date(2026, 6, 27), 3) is True
    assert is_reminder_day(date(2026, 6, 26), 3) is False


def test_month_end_reminders_enqueue_school_and_tutor_notifications(db, users):
    today = date(2099, 1, 31)
    awaiting_school = _create_report(db, users, status=WorkStatus.AWAITING_SCHOOL, target_month=today.strftime("%Y-%m"))
    second_assignment = Assignment(tutor_id=users["tutor"].id, parent_id=users["school"].id, student_name="Step3生徒2")
    db.add(second_assignment)
    db.flush()
    draft = WorkReport(
        assignment_id=second_assignment.id,
        tutor_id=users["tutor"].id,
        target_month=today.strftime("%Y-%m"),
        form_type="monthly_dispatch",
        form_data={"lines": []},
        status=WorkStatus.DRAFT,
        current_approver_role="tutor",
    )
    db.add(draft)
    db.commit()

    count = enqueue_month_end_reminders(db, today=today)

    notifications = list(db.scalars(select(WorkNotification).order_by(WorkNotification.created_at)))
    assert count == 2
    assert {notification.user_id for notification in notifications} == {users["school"].id, users["tutor"].id}
    assert {notification.report_id for notification in notifications} == {awaiting_school.id, draft.id}


def test_daily_stale_check_sets_stale_since_without_overwriting(db, users):
    fresh_stale = _create_report(db, users, status=WorkStatus.AWAITING_SCHOOL, target_month=_previous_month())
    already_set = _create_report(db, users, status=WorkStatus.DRAFT, target_month="2000-01")
    original = datetime(2026, 1, 1)
    already_set.stale_since = original
    db.commit()

    daily_stale_check(db)
    db.refresh(fresh_stale)
    db.refresh(already_set)

    assert fresh_stale.stale_since is not None
    assert already_set.stale_since == original
