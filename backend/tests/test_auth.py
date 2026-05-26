# === Phase 2: 認証・認可 START ===
from datetime import date, datetime, time, timedelta, timezone

from jose import jwt

from app.config import settings
from app.core.security import create_access_token
from app.models import Assignment, Invitation, LessonReport, User


def test_login_success_and_me(client):
    res = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "Passw0rd!"})
    assert res.status_code == 200
    assert "access_token" in res.cookies
    assert res.json()["role"] == "tutor"
    assert res.json()["display_name"] == "Tutor"
    auth = {"Authorization": f"Bearer {res.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["email"] == "tutor@example.com"


def test_login_token_expires_in_8_hours(client):
    before = datetime.now(timezone.utc)
    res = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "Passw0rd!"})
    assert res.status_code == 200
    payload = jwt.decode(res.json()["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc)
    delta = expires_at - before
    assert timedelta(hours=7, minutes=59) <= delta <= timedelta(hours=8, minutes=1)


def test_login_failure(client):
    res = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "bad"})
    assert res.status_code == 401


def test_invalid_token_rejected(client):
    res = client.get("/api/auth/me", headers={"Authorization": "Bearer bad-token"})
    assert res.status_code == 401


def test_page_route_redirects_to_login_when_unauthenticated(client):
    res = client.get("/parent/reports", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_page_route_redirects_to_login_on_role_mismatch(client):
    client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "Passw0rd!"})
    res = client.get("/parent/reports", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_page_route_uses_login_cookie_after_reload(client):
    client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "Passw0rd!"})
    res = client.get("/tutor/reports")
    assert res.status_code == 200
    assert "Tutor Reports" in res.text


def test_parent_reports_page_includes_all_child_assignments(client, db):
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()
    db.add(Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name="Second Student"))
    db.commit()

    client.post("/api/auth/login", data={"username": "parent@example.com", "password": "Passw0rd!"})
    res = client.get("/parent/reports")
    assert res.status_code == 200
    assert "parentAssignments" in res.text
    assert "Student" in res.text
    assert "Second Student" in res.text


def test_admin_page_role_mismatch_redirects_to_login(client):
    client.post("/api/auth/login", data={"username": "receiver@example.com", "password": "Passw0rd!"})
    res = client.get("/admin/users", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/login"


def test_role_forbidden(client):
    res = client.post("/api/users", json={"email": "x@example.com", "role": "tutor", "display_name": "X"}, headers={"Authorization": f"Bearer {create_access_token('00000000-0000-0000-0000-000000000000')}"})
    assert res.status_code in {401, 403}


def test_parent_register_with_invitation(client, db):
    assignment = db.query(Assignment).first()
    assignment.parent_id = None
    invitation = Invitation(
        email="new-parent@example.com",
        role="parent",
        assignment_id=assignment.id,
        token="valid-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(invitation)
    db.commit()

    info = client.get("/api/auth/register?token=valid-token")
    assert info.status_code == 200
    assert info.json()["email"] == "new-parent@example.com"
    assert info.json()["student_name"] == assignment.student_name

    registered = client.post("/api/auth/register", json={
        "token": "valid-token",
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    created = db.query(User).filter(User.email == "new-parent@example.com").one()
    db.refresh(assignment)
    db.refresh(invitation)
    assert created.role == "parent"
    assert created.display_name == f"{assignment.student_name}の保護者"
    assert assignment.parent_id == created.id
    assert invitation.accepted_at is not None


def test_register_does_not_replace_current_admin_session(client, db):
    client.post("/api/auth/login", data={"username": "master@example.com", "password": "Passw0rd!"})
    assignment = db.query(Assignment).first()
    assignment.parent_id = None
    invitation = Invitation(
        email="session-parent@example.com",
        role="parent",
        assignment_id=assignment.id,
        token="session-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(invitation)
    db.commit()

    registered = client.post("/api/auth/register", json={
        "token": "session-token",
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    assert "access_token" not in registered.cookies

    admin_page = client.get("/admin/users", follow_redirects=False)
    assert admin_page.status_code == 200


def test_register_updates_existing_assignment_reports(client, db):
    assignment = db.query(Assignment).first()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=None,
        lesson_date=date(2026, 5, 1),
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="lesson",
        target_month="2026-05",
    )
    assignment.parent_id = None
    invitation = Invitation(
        email="report-parent@example.com",
        role="parent",
        assignment_id=assignment.id,
        token="report-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add_all([report, invitation])
    db.commit()

    registered = client.post("/api/auth/register", json={
        "token": "report-token",
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    created = db.query(User).filter(User.email == "report-parent@example.com").one()
    db.refresh(report)
    assert report.parent_id == created.id


def test_parent_register_without_assignment_uses_email_local_part(client, db):
    invitation = Invitation(
        email="satoshi@example.com",
        role="parent",
        assignment_id=None,
        token="no-assignment-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db.add(invitation)
    db.commit()

    registered = client.post("/api/auth/register", json={
        "token": "no-assignment-token",
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    created = db.query(User).filter(User.email == "satoshi@example.com").one()
    assert created.display_name == "satoshi"
# === Phase 2 END ===
