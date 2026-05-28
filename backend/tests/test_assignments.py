from uuid import UUID

from app.models import Assignment, Invitation, User
from app.core.security import hash_password
from tests.conftest import token


def test_admin_can_create_assignment_with_parent(client, db):
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    parent = db.query(User).filter(User.role == "parent").first()

    res = client.post(
        "/api/assignments",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"tutor_id": str(tutor.id), "student_name": "新規 生徒", "parent_id": str(parent.id)},
    )
    assert res.status_code == 200
    assert res.json()["parent_id"] == str(parent.id)


def test_admin_can_link_parent_to_assignment(client, db):
    master_token = token(client, "master@example.com")
    parent = db.query(User).filter(User.role == "parent").first()
    assignment = db.query(Assignment).first()
    assignment.parent_id = None
    db.commit()

    res = client.patch(
        f"/api/assignments/{assignment.id}",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"parent_id": str(parent.id)},
    )
    assert res.status_code == 200
    assert res.json()["parent_id"] == str(parent.id)


def test_admin_can_add_existing_student_to_another_tutor(client, db):
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    second_tutor = User(
        email="tutor2@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="Tutor 2",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(second_tutor)
    db.commit()

    res = client.post(
        "/api/assignments",
        headers={"Authorization": f"Bearer {master_token}"},
        json={
            "tutor_id": str(second_tutor.id),
            "student_name": assignment.student_name,
            "parent_id": str(assignment.parent_id),
        },
    )

    assert res.status_code == 200
    assert res.json()["tutor_id"] == str(second_tutor.id)
    assert res.json()["student_name"] == assignment.student_name
    assert res.json()["parent_id"] == str(assignment.parent_id)


def test_assignment_duplicate_tutor_and_student_returns_409(client, db):
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()

    res = client.post(
        "/api/assignments",
        headers={"Authorization": f"Bearer {master_token}"},
        json={
            "tutor_id": str(assignment.tutor_id),
            "student_name": assignment.student_name,
            "parent_id": str(assignment.parent_id),
        },
    )

    assert res.status_code == 409


def test_inactive_assignment_is_hidden_from_tutor_assignments(client, db):
    master_token = token(client, "master@example.com")
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()

    res = client.patch(
        f"/api/assignments/{assignment.id}",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"is_active": False},
    )
    assert res.status_code == 200

    tutor_assignments = client.get("/api/assignments", headers={"Authorization": f"Bearer {tutor_token}"})
    assert tutor_assignments.status_code == 200
    assert tutor_assignments.json() == []


def test_create_assignment_links_existing_parent_by_email(client, db, monkeypatch):
    async def fake_send(self, to, subject, body):
        raise AssertionError("existing parent should not receive invitation email")

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    parent = db.query(User).filter(User.email == "parent@example.com").one()

    res = client.post(
        "/api/assignments",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"tutor_id": str(tutor.id), "student_name": "メール 生徒", "parent_email": parent.email},
    )

    assert res.status_code == 200
    assert res.json()["parent_id"] == str(parent.id)
    assert res.json()["parent_email"] == parent.email
    invitation = db.query(Invitation).filter(Invitation.assignment_id == UUID(res.json()["id"])).one()
    assert invitation.accepted_at is not None


def test_tutor_can_create_assignment_with_parent_invitation(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    tutor_token = token(client, "tutor@example.com")
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()

    res = client.post(
        "/api/assignments",
        headers={"Authorization": f"Bearer {tutor_token}"},
        json={"tutor_id": str(tutor.id), "student_name": "講師追加 生徒", "parent_email": "new-parent@example.com"},
    )

    assert res.status_code == 200
    assert res.json()["tutor_id"] == str(tutor.id)
    assert res.json()["parent_id"] is None
    invitation = db.query(Invitation).filter(Invitation.email == "new-parent@example.com").one()
    assert str(invitation.assignment_id) == res.json()["id"]
    assert sent and sent[-1][0] == "new-parent@example.com"
