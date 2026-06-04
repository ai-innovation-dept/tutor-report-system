"""契約管理 API（/api/w/contracts）のテスト。"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
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


def _add_user(db, email, role):
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=["new"],
    )
    db.add(user)
    db.commit()
    return user


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture()
def setup(db):
    master = _add_user(db, "master@x.example.com", "admin_master")
    tutor = _add_user(db, "tutor@x.example.com", "tutor")
    school = _add_user(db, "school@x.example.com", "school")
    return {"master": master, "tutor": tutor, "school": school}


def _payload(setup, **overrides):
    data = {
        "tutor_id": str(setup["tutor"].id),
        "school_id": str(setup["school"].id),
        "customer_id": "9999",
        "our_staff": "佐藤麻子",
        "contract_start": "2026-04-01",
        "contract_end": "2027-03-31",
        "monthly_minutes": 600,
        "weekly_lessons": 3,
        "shift_note": "月9:30-",
        "work_content": "数学指導",
        "has_scoring": True,
        "tasks": [{"task_name": "数学指導", "task_id": "11111", "contract_id": "99992601"}],
    }
    data.update(overrides)
    return data


class TestContractCreate:
    def test_create_ok(self, client, db, setup):
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["tutor_name"] == "tutorユーザー"
        assert body["school_name"] == "schoolユーザー"
        assert body["has_scoring"] is True
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["task_id"] == "11111"
        # assignment が自動作成され紐付く
        assert db.query(Assignment).filter_by(tutor_id=setup["tutor"].id, parent_id=setup["school"].id).count() == 1

    def test_create_reuses_existing_assignment(self, client, db, setup):
        # 既存 assignment があれば再利用（重複作成しない）
        db.add(Assignment(tutor_id=setup["tutor"].id, parent_id=setup["school"].id, student_name="既存", system_type="new"))
        db.commit()
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        assert db.query(Assignment).filter_by(tutor_id=setup["tutor"].id, parent_id=setup["school"].id).count() == 1

    def test_duplicate_pair_rejected(self, client, db, setup):
        headers = _auth(client, "master@x.example.com")
        assert client.post("/api/w/contracts", json=_payload(setup), headers=headers).status_code == 201
        res = client.post("/api/w/contracts", json=_payload(setup), headers=headers)
        assert res.status_code == 409

    def test_tutor_must_be_tutor(self, client, db, setup):
        other = _add_user(db, "o@x.example.com", "office")
        res = client.post("/api/w/contracts", json=_payload(setup, tutor_id=str(other.id)), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422

    def test_school_must_be_school(self, client, db, setup):
        other = _add_user(db, "o@x.example.com", "office")
        res = client.post("/api/w/contracts", json=_payload(setup, school_id=str(other.id)), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422

    def test_first_task_required(self, client, db, setup):
        res = client.post("/api/w/contracts", json=_payload(setup, tasks=[]), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422

    def test_non_admin_forbidden(self, client, db, setup):
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 403


class TestContractListGetUpdateDelete:
    def _create(self, client, setup, **ov):
        return client.post("/api/w/contracts", json=_payload(setup, **ov), headers=_auth(client, "master@x.example.com")).json()

    def test_list_and_get(self, client, db, setup):
        created = self._create(client, setup)
        headers = _auth(client, "master@x.example.com")
        listed = client.get("/api/w/contracts", headers=headers).json()
        assert created["id"] in [c["id"] for c in listed]
        got = client.get(f"/api/w/contracts/{created['id']}", headers=headers)
        assert got.status_code == 200
        assert got.json()["customer_id"] == "9999"

    def test_update_detail(self, client, db, setup):
        created = self._create(client, setup)
        headers = _auth(client, "master@x.example.com")
        res = client.patch(
            f"/api/w/contracts/{created['id']}",
            json={"our_staff": "新担当", "monthly_minutes": 720,
                  "tasks": [{"task_name": "A", "task_id": "1", "contract_id": "2"},
                            {"task_name": "B", "task_id": "3", "contract_id": "4"}]},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["our_staff"] == "新担当"
        assert body["monthly_minutes"] == 720
        assert len(body["tasks"]) == 2

    def test_delete_is_logical(self, client, db, setup):
        created = self._create(client, setup)
        headers = _auth(client, "master@x.example.com")
        res = client.delete(f"/api/w/contracts/{created['id']}", headers=headers)
        assert res.status_code == 200
        profile = db.get(WorkAssignmentProfile, __import__("uuid").UUID(created["id"]))
        assert profile.is_active is False
        # 無効でも一覧には残る
        listed = client.get("/api/w/contracts", headers=headers).json()
        assert created["id"] in [c["id"] for c in listed]
