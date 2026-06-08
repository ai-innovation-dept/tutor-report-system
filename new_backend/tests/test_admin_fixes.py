"""経理画面改修（ユーザー一覧全量表示・ロール変更・学校スキップ・学校リマインド）のテスト。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkNotification, WorkReport
from app.services.reminder_service import enqueue_school_approval_reminders
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
def client():
    return TestClient(app)


_UNSET = object()


def _add_user(db, email, role, *, allowed_systems=_UNSET, is_active=True):
    # 既定では新システム所属（ログイン可）。明示的に None / ["legacy"] を渡せば未所属を再現できる。
    if allowed_systems is _UNSET:
        allowed_systems = ["new"]
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=allowed_systems,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    return user


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


# ---------------------------------------------------------------------------
# ユーザー一覧（全量表示）
# ---------------------------------------------------------------------------

class TestUserListAllUsers:
    def test_only_new_system_users_are_listed(self, client, db):
        # 所属の基準は allowed_systems。新システム(new)に登録のあるユーザーのみ一覧に出る。
        _add_user(db, "master@x.example.com", "admin_master", allowed_systems=["new"])
        _add_user(db, "legacy@x.example.com", "tutor", allowed_systems=["legacy"])
        _add_user(db, "nosys@x.example.com", "school", allowed_systems=None)

        res = client.get("/api/w/users", headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 200
        emails = {u["email"] for u in res.json()["items"]}
        assert "master@x.example.com" in emails
        assert "legacy@x.example.com" not in emails
        assert "nosys@x.example.com" not in emails

    def test_pagination_meta_and_role_counts(self, client, db):
        _add_user(db, "master@x.example.com", "admin_master")
        _add_user(db, "t1@x.example.com", "tutor")

        res = client.get("/api/w/users", headers=_auth(client, "master@x.example.com"))
        body = res.json()
        assert body["page"] == 1
        assert body["total_pages"] >= 1
        assert body["role_counts"]["all"] == body["total"]
        assert body["role_counts"]["tutor"] == 1
        assert body["active_admin_master_count"] == 1

    def test_roles_csv_filter(self, client, db):
        _add_user(db, "master@x.example.com", "admin_master")
        _add_user(db, "t1@x.example.com", "tutor")
        _add_user(db, "s1@x.example.com", "school")

        res = client.get("/api/w/users?roles=tutor", headers=_auth(client, "master@x.example.com"))
        items = res.json()["items"]
        assert len(items) == 1
        assert items[0]["email"] == "t1@x.example.com"


# ---------------------------------------------------------------------------
# ロール変更・有効化/無効化・削除
# ---------------------------------------------------------------------------

class TestUserManagementEndpoints:
    def test_update_staff_roles(self, client, db):
        _add_user(db, "master@x.example.com", "admin_master")
        staff = _add_user(db, "staff@x.example.com", "sales")

        res = client.patch(
            f"/api/w/users/{staff.id}/roles",
            json={"roles": ["sales", "office"]},
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 200
        assert set(res.json()["roles"]) == {"sales", "office"}

    def test_cannot_change_non_staff_roles(self, client, db):
        _add_user(db, "master@x.example.com", "admin_master")
        tutor = _add_user(db, "t1@x.example.com", "tutor")

        res = client.patch(
            f"/api/w/users/{tutor.id}/roles",
            json={"roles": ["office"]},
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 409

    def test_disable_and_enable(self, client, db):
        _add_user(db, "master@x.example.com", "admin_master")
        target = _add_user(db, "t1@x.example.com", "tutor")
        headers = _auth(client, "master@x.example.com")

        res = client.patch(f"/api/w/users/{target.id}/disable", headers=headers)
        assert res.status_code == 200 and res.json()["is_active"] is False
        res = client.patch(f"/api/w/users/{target.id}/enable", headers=headers)
        assert res.status_code == 200 and res.json()["is_active"] is True

    def test_cannot_disable_last_admin_master(self, client, db):
        master = _add_user(db, "master@x.example.com", "admin_master")
        headers = _auth(client, "master@x.example.com")

        res = client.patch(f"/api/w/users/{master.id}/disable", headers=headers)
        assert res.status_code == 409

    def test_delete_is_soft_delete(self, client, db):
        _add_user(db, "master@x.example.com", "admin_master")
        target = _add_user(db, "t1@x.example.com", "tutor")
        headers = _auth(client, "master@x.example.com")

        res = client.delete(f"/api/w/users/{target.id}", headers=headers)
        assert res.status_code == 200
        db.refresh(target)
        assert target.deleted_at is not None
        assert target.is_active is False

        res = client.get("/api/w/users", headers=headers)
        assert "t1@x.example.com" not in {u["email"] for u in res.json()["items"]}


# ---------------------------------------------------------------------------
# 学校スキップ（提出時に学校確認を飛ばす）
# ---------------------------------------------------------------------------

class TestSkipSchoolOnSubmit:
    def _setup_assignment(self, db, *, skip):
        tutor = _add_user(db, "tutor@x.example.com", "tutor")
        school = _add_user(db, "school@x.example.com", "school")
        # 学校スキップは学校ユーザー単位（users.skip_parent_approval）で管理する
        school.skip_parent_approval = skip
        assignment = Assignment(
            tutor_id=tutor.id,
            parent_id=school.id,
            student_name="生徒A",
            system_type="new",
        )
        db.add(assignment)
        db.commit()
        return tutor, assignment

    def _create_report(self, client, assignment):
        headers = _auth(client, "tutor@x.example.com")
        res = client.post(
            "/api/w/reports",
            json={
                "assignment_id": str(assignment.id),
                "target_month": "2026-06",
                "form_type": "monthly_dispatch",
                "form_data": {"lines": []},
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text
        return res.json()["id"], headers

    def test_submit_goes_to_office_when_skip_enabled(self, client, db):
        _, assignment = self._setup_assignment(db, skip=True)
        report_id, headers = self._create_report(client, assignment)

        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE

    def test_submit_goes_to_school_when_skip_disabled(self, client, db):
        _, assignment = self._setup_assignment(db, skip=False)
        report_id, headers = self._create_report(client, assignment)

        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL


# ---------------------------------------------------------------------------
# 学校承認リマインド（紐付け単位の設定）
# ---------------------------------------------------------------------------

class TestSchoolApprovalReminder:
    def _setup(self, db, *, reminder_enabled=True, days_after=1, count=2, submitted_days_ago=2):
        tutor = _add_user(db, "tutor@x.example.com", "tutor")
        school = _add_user(db, "school@x.example.com", "school")
        assignment = Assignment(
            tutor_id=tutor.id,
            parent_id=school.id,
            student_name="生徒A",
            system_type="new",
            reminder_enabled=reminder_enabled,
            reminder_days_after=days_after,
            reminder_count=count,
        )
        db.add(assignment)
        db.flush()
        report = WorkReport(
            assignment_id=assignment.id,
            tutor_id=tutor.id,
            target_month="2026-06",
            form_type="monthly_dispatch",
            form_data={"lines": []},
            status=WorkStatus.AWAITING_SCHOOL,
            current_approver_role="school",
            submitted_at=datetime.now(timezone.utc) - timedelta(days=submitted_days_ago),
        )
        db.add(report)
        db.commit()
        return school, report

    def _reminder_count(self, db, report):
        return (
            db.query(WorkNotification)
            .filter(
                WorkNotification.report_id == report.id,
                WorkNotification.type == "reminder_school_approval",
            )
            .count()
        )

    def test_reminder_sent_when_due(self, db):
        school, report = self._setup(db)
        sent = enqueue_school_approval_reminders(db)
        db.commit()
        assert sent == 1
        assert self._reminder_count(db, report) == 1

    def test_no_duplicate_same_day(self, db):
        _, report = self._setup(db)
        assert enqueue_school_approval_reminders(db) == 1
        db.commit()
        assert enqueue_school_approval_reminders(db) == 0
        assert self._reminder_count(db, report) == 1

    def test_disabled_reminder_not_sent(self, db):
        _, report = self._setup(db, reminder_enabled=False)
        assert enqueue_school_approval_reminders(db) == 0

    def test_not_due_yet(self, db):
        _, report = self._setup(db, days_after=5, submitted_days_ago=2)
        assert enqueue_school_approval_reminders(db) == 0
