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


def test_parent_cannot_create_invitation(client, db):
    parent_token = token(client, "parent@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {parent_token}"},
        json={"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒"},
    )
    assert res.status_code == 403
