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
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["task_id"] == "11111"
        assert body["tasks"][0]["task_format"] == "minutes"
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


class TestContractForTutor:
    def _create(self, client, setup, **ov):
        return client.post("/api/w/contracts", json=_payload(setup, **ov), headers=_auth(client, "master@x.example.com")).json()

    def test_for_tutor_returns_column_definition(self, client, db, setup):
        # 委託業務②を「回数＋分数」形式にすると 回・分 の2列が生成される
        self._create(
            client, setup,
            tasks=[
                {"task_name": "数学指導", "task_id": "T1", "contract_id": "C1"},
                {"task_name": "採点", "task_id": "T2", "contract_id": "C2", "task_format": "count_minutes"},
            ],
        )
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body) == 1
        entry = body[0]
        assert entry["school_id"] == str(setup["school"].id)
        keys = [c["key"] for c in entry["column_definition"]]
        # 固定先頭 → 委託業務①(分のみ) → 委託業務②(回＋分=1列) → 固定末尾 の順
        assert keys == [
            "date", "start", "end", "subject_period",
            "task_minutes_1",
            "task_2",
            "break_minutes", "commute_fee", "note",
        ]
        col1 = next(c for c in entry["column_definition"] if c["key"] == "task_minutes_1")
        assert col1["label"] == "数学指導（分）"
        assert col1["summable"] is True
        assert col1["task_id"] == "T1"
        # 回数＋分数は type=count_minutes の1列。見出しは（回）、1セルに回・分の2値を併記
        col2 = next(c for c in entry["column_definition"] if c["key"] == "task_2")
        assert col2["type"] == "count_minutes"
        assert col2["label"] == "採点（回）"
        assert col2["count_key"] == "task_count_2"
        assert col2["minutes_key"] == "task_minutes_2"
        assert col2["minutes_label"] == "採点（分）"
        assert col2["summable"] is True

    def test_for_tutor_minutes_only_omits_count_column(self, client, db, setup):
        # 分のみ（デフォルト）の委託業務は 分 列のみで 回 列は生成されない
        self._create(client, setup)
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        keys = [c["key"] for c in entry["column_definition"]]
        assert "task_count_1" not in keys
        assert "task_minutes_1" in keys

    def test_for_tutor_only_own_contracts(self, client, db, setup):
        self._create(client, setup)
        other_tutor = _add_user(db, "tutor2@x.example.com", "tutor")
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor2@x.example.com"))
        assert res.status_code == 200
        assert res.json() == []

    def test_for_tutor_requires_tutor_role(self, client, db, setup):
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "master@x.example.com"))
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
