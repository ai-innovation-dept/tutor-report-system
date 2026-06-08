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


def test_new_system_assignment_is_hidden_from_legacy_list(client, db):
    """業務連絡表システム（system_type='new'）の学校紐付けは既存システムの一覧に出さない。"""
    tutor_token = token(client, "tutor@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    # 新システムが作る学校紐付けを模す（student_name に学校名、system_type='new'）
    db.add(Assignment(tutor_id=tutor.id, student_name="渋谷高校", system_type="new"))
    db.commit()

    res = client.get("/api/assignments", headers={"Authorization": f"Bearer {tutor_token}"})
    assert res.status_code == 200
    names = [a["student_name"] for a in res.json()]
    assert "渋谷高校" not in names
    # 既存システムの紐付け（system_type デフォルト 'legacy'）は残る
    assert "Student" in names


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


def test_tutor_cannot_patch_reminder_settings(client, db):
    # リマインダー設定は運営（管理者）画面へ移管したため、講師は編集できない。
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()

    res = client.patch(
        f"/api/assignments/{assignment.id}",
        headers={"Authorization": f"Bearer {tutor_token}"},
        json={"reminder_enabled": True, "reminder_days_after": 3},
    )
    assert res.status_code == 403


def test_admin_roles_can_patch_reminder_settings(client, db):
    # 受付・再鑑・管理者の全運営ロールがリマインダー設定を編集できる。
    assignment = db.query(Assignment).first()
    for email in ("receiver@example.com", "reviewer@example.com", "master@example.com"):
        admin_token = token(client, email)
        res = client.patch(
            f"/api/assignments/{assignment.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"reminder_enabled": True, "reminder_days_after": 5},
        )
        assert res.status_code == 200, email
        assert res.json()["reminder_enabled"] is True
        assert res.json()["reminder_days_after"] == 5


def test_admin_receiver_cannot_patch_non_reminder_fields(client, db):
    # 受付・再鑑はリマインダー設定のみ編集可。生徒名などは変更されない。
    receiver_token = token(client, "receiver@example.com")
    assignment = db.query(Assignment).first()
    original_student_name = assignment.student_name

    res = client.patch(
        f"/api/assignments/{assignment.id}",
        headers={"Authorization": f"Bearer {receiver_token}"},
        json={"student_name": "改ざん 生徒", "reminder_enabled": True},
    )
    assert res.status_code == 200
    assert res.json()["student_name"] == original_student_name
    assert res.json()["reminder_enabled"] is True


def test_tutor_cannot_patch_another_tutors_assignment(client, db):
    from app.core.security import hash_password as hp
    other_tutor = User(
        email="other@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="Other Tutor",
        password_hash=hp("Passw0rd!"),
    )
    db.add(other_tutor)
    db.flush()
    other_assignment = Assignment(tutor_id=other_tutor.id, student_name="Other Student")
    db.add(other_assignment)
    db.commit()

    tutor_token = token(client, "tutor@example.com")
    res = client.patch(
        f"/api/assignments/{other_assignment.id}",
        headers={"Authorization": f"Bearer {tutor_token}"},
        json={"reminder_enabled": True},
    )
    assert res.status_code == 403


def test_parent_cannot_patch_assignment(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()

    res = client.patch(
        f"/api/assignments/{assignment.id}",
        headers={"Authorization": f"Bearer {parent_token}"},
        json={"reminder_enabled": True},
    )
    assert res.status_code == 403
