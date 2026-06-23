import datetime as dt

from app.models import Assignment, LessonReport, ReportStatus, User
from tests.conftest import token


def _add_report(db, status):
    """conftestのtutor＋parent＋assignmentに、指定ステータスの報告書を1件足す。"""
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    assignment = db.query(Assignment).first()
    db.add(LessonReport(
        assignment_id=assignment.id, tutor_id=tutor.id, parent_id=parent.id,
        lesson_date=dt.date(2026, 6, 1), start_time=dt.time(16, 0), end_time=dt.time(17, 0),
        break_minutes=0, content="x", target_month="2026-06", status=status,
    ))
    db.commit()
    return tutor, parent


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


def test_cannot_delete_user_with_active_approval_flow(client, db):
    # 承認フロー進行中（保護者承認待ち）の報告書に関与する講師・保護者は削除できない。
    master_token = token(client, "master@example.com")
    tutor, parent = _add_report(db, ReportStatus.awaiting_parent_approval.value)

    res_parent = client.delete(f"/api/users/{parent.id}", headers={"Authorization": f"Bearer {master_token}"})
    assert res_parent.status_code == 409, res_parent.text
    assert "承認フロー進行中" in res_parent.json()["detail"]

    res_tutor = client.delete(f"/api/users/{tutor.id}", headers={"Authorization": f"Bearer {master_token}"})
    assert res_tutor.status_code == 409, res_tutor.text

    db.expire_all()
    assert db.get(User, tutor.id).deleted_at is None
    assert db.get(User, parent.id).deleted_at is None


def test_can_delete_user_after_flow_finalized(client, db):
    # 最終承認済み（終端）の報告書しか持たないユーザーは削除できる。
    master_token = token(client, "master@example.com")
    tutor, _ = _add_report(db, ReportStatus.admin_approved.value)

    res = client.delete(f"/api/users/{tutor.id}", headers={"Authorization": f"Bearer {master_token}"})
    assert res.status_code == 200, res.text


def test_can_delete_user_with_only_draft_report(client, db):
    # 下書き（未提出＝フロー外）のみのユーザーは削除できる。
    master_token = token(client, "master@example.com")
    tutor, _ = _add_report(db, ReportStatus.draft.value)

    res = client.delete(f"/api/users/{tutor.id}", headers={"Authorization": f"Bearer {master_token}"})
    assert res.status_code == 200, res.text
