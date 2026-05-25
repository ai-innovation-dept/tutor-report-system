from app.models import Assignment, User
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
