from app.models import User
from tests.conftest import token


def test_admin_can_update_staff_roles(client, db):
    master_token = token(client, "master@example.com")
    receiver = db.query(User).filter(User.email == "receiver@example.com").one()

    res = client.patch(
        f"/api/users/{receiver.id}/roles",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"roles": ["admin_receiver", "admin_reviewer"]},
    )

    assert res.status_code == 200
    assert res.json()["role"] == "admin_receiver"
    assert res.json()["roles"] == ["admin_receiver", "admin_reviewer"]
    db.refresh(receiver)
    assert receiver.roles == ["admin_receiver", "admin_reviewer"]


def test_admin_master_cannot_be_combined_with_other_roles(client, db):
    master_token = token(client, "master@example.com")
    receiver = db.query(User).filter(User.email == "receiver@example.com").one()

    res = client.patch(
        f"/api/users/{receiver.id}/roles",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"roles": ["admin_master", "admin_receiver"]},
    )

    assert res.status_code == 422


def test_admin_can_soft_delete_non_admin_user(client, db):
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()

    deleted = client.delete(f"/api/users/{tutor.id}", headers={"Authorization": f"Bearer {master_token}"})
    assert deleted.status_code == 200
    db.refresh(tutor)
    assert tutor.deleted_at is not None
    assert tutor.is_active is False

    listed = client.get("/api/users", headers={"Authorization": f"Bearer {master_token}"})
    assert listed.status_code == 200
    assert all(user["id"] != str(tutor.id) for user in listed.json()["items"])


def test_admin_master_cannot_be_deleted(client, db):
    master_token = token(client, "master@example.com")
    master = db.query(User).filter(User.email == "master@example.com").one()

    res = client.delete(f"/api/users/{master.id}", headers={"Authorization": f"Bearer {master_token}"})

    assert res.status_code == 409


def test_users_list_supports_pagination_search_and_role_filter(client, db):
    master_token = token(client, "master@example.com")

    res = client.get(
        "/api/users?roles=admin_receiver&page=1&per_page=1&search=receiver",
        headers={"Authorization": f"Bearer {master_token}"},
    )

    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["total_pages"] == 1
    assert data["items"][0]["email"] == "receiver@example.com"
    assert data["role_counts"]["admin_receiver"] == 1


def test_admin_can_disable_and_enable_user(client, db):
    master_token = token(client, "master@example.com")
    receiver = db.query(User).filter(User.email == "receiver@example.com").one()

    disabled = client.patch(f"/api/users/{receiver.id}/disable", headers={"Authorization": f"Bearer {master_token}"})
    assert disabled.status_code == 200
    db.refresh(receiver)
    assert receiver.is_active is False

    enabled = client.patch(f"/api/users/{receiver.id}/enable", headers={"Authorization": f"Bearer {master_token}"})
    assert enabled.status_code == 200
    db.refresh(receiver)
    assert receiver.is_active is True


def test_last_active_admin_master_cannot_be_disabled(client, db):
    master_token = token(client, "master@example.com")
    master = db.query(User).filter(User.email == "master@example.com").one()

    res = client.patch(f"/api/users/{master.id}/disable", headers={"Authorization": f"Bearer {master_token}"})

    assert res.status_code == 409
