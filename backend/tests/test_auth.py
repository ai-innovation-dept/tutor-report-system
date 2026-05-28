# === Phase 2: 認証・認可 START ===
from datetime import date, datetime, time, timedelta, timezone

from jose import jwt

from app.config import settings
from app.core.security import create_access_token, verify_password
from app.models import Assignment, Invitation, LessonReport, PasswordResetToken, User


def test_login_success_and_me(client):
    res = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "Passw0rd!"})
    assert res.status_code == 200
    assert "access_token" in res.cookies
    assert res.json()["role"] == "tutor"
    assert res.json()["roles"] == ["tutor"]
    assert res.json()["requires_role_selection"] is False
    assert res.json()["display_name"] == "Tutor"
    auth = {"Authorization": f"Bearer {res.json()['access_token']}"}
    me = client.get("/api/auth/me", headers=auth)
    assert me.status_code == 200
    assert me.json()["email"] == "tutor@example.com"
    assert me.json()["roles"] == ["tutor"]


def test_multi_role_login_requires_role_selection(client, db):
    user = db.query(User).filter(User.email == "receiver@example.com").one()
    user.role = "admin_receiver"
    user.roles = ["admin_receiver", "admin_reviewer"]
    db.commit()

    res = client.post("/api/auth/login", data={"username": "receiver@example.com", "password": "Passw0rd!"})
    assert res.status_code == 200
    assert res.json()["role"] is None
    assert res.json()["roles"] == ["admin_receiver", "admin_reviewer"]
    assert res.json()["requires_role_selection"] is True
    assert res.json()["redirect_url"] == "/select-role"

    selected = client.post("/api/auth/select-role", json={"role": "admin_reviewer"})
    assert selected.status_code == 200
    assert selected.json()["role"] == "admin_reviewer"
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["role"] == "admin_reviewer"
    assert me.json()["roles"] == ["admin_receiver", "admin_reviewer"]


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


def test_admin_approval_and_progress_routes_are_removed(client):
    client.post("/api/auth/login", data={"username": "master@example.com", "password": "Passw0rd!"})
    assert client.get("/admin/approval").status_code == 404
    assert client.get("/admin/progress").status_code == 404


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
    assert created.roles == ["parent"]
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


def test_forgot_password_creates_token_and_sends_email(client, db, monkeypatch):
    sent = []

    async def fake_send(to_email, subject, template_name, context):
        sent.append((to_email, subject, template_name, context))

    monkeypatch.setattr("app.api.auth.send_email_notification", fake_send)
    res = client.post("/api/auth/forgot-password", json={"email": "tutor@example.com"})
    assert res.status_code == 200
    assert res.json()["message"] == "パスワードリセットメールを送信しました"
    reset_token = db.query(PasswordResetToken).join(User).filter(User.email == "tutor@example.com").one()
    assert reset_token.token
    assert reset_token.used_at is None
    assert sent
    assert sent[-1][0] == "tutor@example.com"
    assert sent[-1][2] == "password_reset.txt"
    assert sent[-1][3]["token"] == reset_token.token


def test_forgot_password_finds_email_case_insensitively(client, db, monkeypatch):
    sent = []
    user = db.query(User).filter(User.email == "tutor@example.com").one()
    user.email = "Tutor.Mixed@example.com"
    db.commit()

    async def fake_send(to_email, subject, template_name, context):
        sent.append((to_email, subject, template_name, context))

    monkeypatch.setattr("app.api.auth.send_email_notification", fake_send)
    res = client.post("/api/auth/forgot-password", json={"email": "tutor.mixed@example.com"})
    assert res.status_code == 200
    assert db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).count() == 1
    assert sent and sent[-1][0] == "Tutor.Mixed@example.com"


def test_forgot_password_does_not_reveal_unknown_email(client, db, monkeypatch):
    sent = []

    async def fake_send(to_email, subject, template_name, context):
        sent.append((to_email, subject, template_name, context))

    monkeypatch.setattr("app.api.auth.send_email_notification", fake_send)
    res = client.post("/api/auth/forgot-password", json={"email": "missing@example.com"})
    assert res.status_code == 200
    assert res.json()["message"] == "パスワードリセットメールを送信しました"
    assert db.query(PasswordResetToken).count() == 0
    assert sent == []


def test_reset_password_token_info_and_update(client, db):
    user = db.query(User).filter(User.email == "tutor@example.com").one()
    reset_token = PasswordResetToken(
        user_id=user.id,
        token="reset-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(reset_token)
    db.commit()

    info = client.get("/api/auth/reset-password?token=reset-token")
    assert info.status_code == 200
    assert info.json() == {"valid": True, "email": "tutor@example.com", "reason": None}

    changed = client.post("/api/auth/reset-password", json={"token": "reset-token", "new_password": "NewPassw0rd!"})
    assert changed.status_code == 200
    assert changed.json()["message"] == "パスワードを変更しました"
    db.refresh(user)
    db.refresh(reset_token)
    assert verify_password("NewPassw0rd!", user.password_hash)
    assert reset_token.used_at is not None
    login = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "NewPassw0rd!"})
    assert login.status_code == 200


def test_reset_password_rejects_expired_used_and_missing_tokens(client, db):
    user = db.query(User).filter(User.email == "tutor@example.com").one()
    expired = PasswordResetToken(
        user_id=user.id,
        token="expired-token",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    used = PasswordResetToken(
        user_id=user.id,
        token="used-token",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        used_at=datetime.now(timezone.utc),
    )
    db.add_all([expired, used])
    db.commit()

    expired_info = client.get("/api/auth/reset-password?token=expired-token")
    assert expired_info.status_code == 200
    assert expired_info.json()["reason"] == "expired"
    used_info = client.get("/api/auth/reset-password?token=used-token")
    assert used_info.status_code == 200
    assert used_info.json()["reason"] == "used"
    missing_info = client.get("/api/auth/reset-password?token=missing-token")
    assert missing_info.status_code == 200
    assert missing_info.json()["reason"] == "not_found"

    assert client.post("/api/auth/reset-password", json={"token": "expired-token", "new_password": "NewPassw0rd!"}).status_code == 410
    assert client.post("/api/auth/reset-password", json={"token": "used-token", "new_password": "NewPassw0rd!"}).status_code == 409
    assert client.post("/api/auth/reset-password", json={"token": "missing-token", "new_password": "NewPassw0rd!"}).status_code == 404
# === Phase 2 END ===
