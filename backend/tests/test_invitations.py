from app.models import Assignment, Invitation, User
from tests.conftest import token


def test_admin_can_create_and_resend_invitation(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()

    payload = {"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒"}
    created = client.post("/api/invitations", headers={"Authorization": f"Bearer {master_token}"}, json=payload)
    assert created.status_code == 200
    invitation = db.query(Invitation).filter(Invitation.email == "invitee@example.com").one()
    first_token = invitation.token
    assert invitation.assignment_id is not None
    assert invitation.assignment.student_name == "招待 生徒"
    assert created.json()["student_name"] == "招待 生徒"
    assert created.json()["tutor_id"] == str(tutor.id)
    assert created.json()["tutor_name"] == tutor.display_name
    assert sent and sent[-1][0] == "invitee@example.com"
    assert f"担当講師：{tutor.display_name}" in sent[-1][2]
    assert "担当生徒：招待 生徒" in sent[-1][2]

    resent = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒 再送"},
    )
    assert resent.status_code == 200
    db.expire_all()
    invitations = db.query(Invitation).filter(Invitation.email == "invitee@example.com").all()
    assert len(invitations) == 1
    assert invitations[0].token != first_token
    assert invitations[0].assignment.student_name == "招待 生徒 再送"


def test_admin_can_add_student_to_existing_parent(client, db, monkeypatch):
    async def fake_send(self, to, subject, body):
        raise AssertionError("existing parent should not receive invitation email")

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    parent = db.query(User).filter(User.email == "parent@example.com").one()

    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": parent.email, "tutor_id": str(tutor.id), "student_name": "追加 生徒"},
    )
    assert res.status_code == 200
    assert res.json()["message"] == "既存の保護者アカウントに生徒を紐付けました"
    assignment = db.query(Assignment).filter(Assignment.student_name == "追加 生徒").one()
    assert assignment.parent_id == parent.id
    assert assignment.tutor_id == tutor.id
    invitation = db.query(Invitation).filter(Invitation.assignment_id == assignment.id).one()
    assert invitation.accepted_at is not None


def test_admin_can_invite_tutor_and_register(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    existing_tutor = db.query(User).filter(User.role == "tutor").first()
    existing_tutor.tutor_no = "10002"
    db.commit()
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "tutor3@example.com", "role": "tutor", "display_name": "田中三郎"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "tutor"
    assert res.json()["tutor_no"] == "10003"
    assert "講師No：10003" in sent[-1][2]

    invitation = db.query(Invitation).filter(Invitation.email == "tutor3@example.com").one()
    info = client.get(f"/api/auth/register?token={invitation.token}")
    assert info.status_code == 200
    assert info.json()["role"] == "tutor"
    assert info.json()["display_name"] == "田中三郎"

    registered = client.post("/api/auth/register", json={
        "token": invitation.token,
        "display_name": "田中 三郎",
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    user = db.query(User).filter(User.email == "tutor3@example.com").one()
    assert user.roles == ["tutor"]
    assert user.display_name == "田中 三郎"
    assert user.tutor_no == "10003"


def test_admin_can_invite_staff_and_register(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "receiver2@example.com", "role": "admin_receiver", "display_name": "山田受付"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "admin_receiver"
    assert "ロール：受付担当" in sent[-1][2]

    invitation = db.query(Invitation).filter(Invitation.email == "receiver2@example.com").one()
    registered = client.post("/api/auth/register", json={
        "token": invitation.token,
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    user = db.query(User).filter(User.email == "receiver2@example.com").one()
    assert user.roles == ["admin_receiver"]
    assert user.display_name == "山田受付"


def test_parent_cannot_create_invitation(client, db):
    parent_token = token(client, "parent@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {parent_token}"},
        json={"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒"},
    )
    assert res.status_code == 403
