"""契約管理 API（/api/w/contracts）のテスト。"""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
from app.services import contract_import_service as cis
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
        assert body["scoring_enabled"] is False
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
        # 委託業務（分のみ）＋採点専用欄を有効化 → 末尾に採点（回）の1列(count_minutes)
        self._create(
            client, setup,
            tasks=[
                {"task_name": "数学指導", "task_id": "T1", "contract_id": "C1"},
                {"task_name": "教科会", "task_id": "T2", "contract_id": "C2"},
            ],
            scoring_enabled=True,
            scoring_task_id="S1",
            scoring_contract_id="SC1",
        )
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 200, res.text
        body = res.json()
        assert len(body) == 1
        entry = body[0]
        assert entry["school_id"] == str(setup["school"].id)
        keys = [c["key"] for c in entry["column_definition"]]
        # 固定先頭 → 委託業務①②(分のみ) → 採点(回＋分=1列) → 固定末尾 の順
        assert keys == [
            "date", "start", "end", "subject_period",
            "task_minutes_1", "task_minutes_2",
            "scoring",
            "break_minutes", "commute_fee", "note",
        ]
        col1 = next(c for c in entry["column_definition"] if c["key"] == "task_minutes_1")
        assert col1["label"] == "数学指導（分）"
        assert col1["summable"] is True
        assert col1["task_id"] == "T1"
        # 採点は type=count_minutes の1列。見出しは（回）、1セルに回・分の2値を併記
        scoring = next(c for c in entry["column_definition"] if c["key"] == "scoring")
        assert scoring["type"] == "count_minutes"
        assert scoring["label"] == "採点（回）"
        assert scoring["count_key"] == "scoring_count"
        assert scoring["minutes_key"] == "scoring_minutes"
        assert scoring["minutes_label"] == "採点（分）"
        assert scoring["task_id"] == "S1"
        assert scoring["contract_id"] == "SC1"
        assert scoring["summable"] is True

    def test_for_tutor_without_scoring_omits_scoring_column(self, client, db, setup):
        # 採点無効（デフォルト）なら採点列は生成されない
        self._create(client, setup)
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        keys = [c["key"] for c in entry["column_definition"]]
        assert "scoring" not in keys
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


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cis.headers())
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in cis.headers()})
    return buf.getvalue().encode("utf-8-sig")


def _csv_row(tutor_no, school_name, **over):
    row = {
        cis.TUTOR_NO: tutor_no,
        cis.SCHOOL_NAME: school_name,
        cis.CUSTOMER_ID: "9999",
        cis._task_name_h(1): "数学指導",
        cis._task_id_h(1): "T1",
    }
    row.update(over)
    return row


class TestContractImport:
    @pytest.fixture()
    def import_setup(self, db, setup):
        tutor = _add_user(db, "t100@x.example.com", "tutor")
        tutor.user_no = "T100"
        tutor.display_name = "山田太郎"
        school = _add_user(db, "shibuya@x.example.com", "school")
        school.display_name = "渋谷高校"
        db.commit()
        return {**setup, "imp_tutor": tutor, "imp_school": school}

    def _upload(self, client, data: bytes):
        return client.post(
            "/api/w/contracts/import",
            files={"file": ("contracts.csv", data, "text/csv")},
            headers=_auth(client, "master@x.example.com"),
        )

    def test_template_download(self, client, setup):
        res = client.get("/api/w/contracts/import-template", headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        text = res.content.decode("utf-8-sig")
        assert cis.TUTOR_NO in text.splitlines()[0]

    def test_import_creates_then_upserts(self, client, db, import_setup):
        # 新規取り込み
        res = self._upload(client, _csv_bytes([_csv_row("T100", "渋谷高校", **{cis.OUR_STAFF: "旧担当"})]))
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 1, "created": 1, "updated": 0}
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.our_staff == "旧担当"
        assert profile.task_name_1 == "数学指導"
        # 同一(講師×学校)を再取り込み → upsertで更新
        res2 = self._upload(client, _csv_bytes([_csv_row("T100", "渋谷高校", **{cis.OUR_STAFF: "新担当"})]))
        assert res2.status_code == 200, res2.text
        assert res2.json() == {"imported": 1, "created": 0, "updated": 1}
        db.refresh(profile)
        assert profile.our_staff == "新担当"

    def test_import_scoring_enabled(self, client, db, import_setup):
        res = self._upload(client, _csv_bytes([
            _csv_row("T100", "渋谷高校", **{cis.SCORING_ENABLED: "有", cis.SCORING_TASK_ID: "S1"})]))
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.scoring_enabled is True
        assert profile.scoring_task_id == "S1"

    def test_import_all_or_nothing(self, client, db, import_setup):
        # 1行目は有効、2行目は講師番号が不正 → 全件中止（何も登録しない）
        data = _csv_bytes([
            _csv_row("T100", "渋谷高校"),
            _csv_row("UNKNOWN", "渋谷高校"),
        ])
        res = self._upload(client, data)
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert any("UNKNOWN" in e for e in detail["errors"])
        assert db.query(WorkAssignmentProfile).count() == 0

    def test_import_skips_example_row(self, client, db, import_setup):
        # 講師番号が#始まりの記入例行はスキップ、有効行のみ取り込む
        data = _csv_bytes([
            _csv_row("#T0001", "渋谷高校"),
            _csv_row("T100", "渋谷高校"),
        ])
        res = self._upload(client, data)
        assert res.status_code == 200, res.text
        assert res.json()["imported"] == 1

    def test_import_first_task_required(self, client, db, import_setup):
        row = _csv_row("T100", "渋谷高校")
        row[cis._task_name_h(1)] = ""
        row[cis._task_id_h(1)] = ""
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 400
        assert db.query(WorkAssignmentProfile).count() == 0

    def test_import_non_admin_forbidden(self, client, import_setup):
        res = client.post(
            "/api/w/contracts/import",
            files={"file": ("c.csv", _csv_bytes([_csv_row("T100", "渋谷高校")]), "text/csv")},
            headers=_auth(client, "t100@x.example.com"),
        )
        assert res.status_code == 403


class TestSalesContractAccess:
    """承認フロー変更に伴い、営業ロールも契約管理を利用できる。"""

    def test_sales_can_create_and_list_contracts(self, client, db, setup):
        _add_user(db, "sales@x.example.com", "sales")
        created = client.post(
            "/api/w/contracts", json=_payload(setup),
            headers=_auth(client, "sales@x.example.com"),
        )
        assert created.status_code == 201, created.text
        listed = client.get("/api/w/contracts", headers=_auth(client, "sales@x.example.com"))
        assert listed.status_code == 200
        assert len(listed.json()) == 1

    def test_office_still_forbidden_from_contracts(self, client, db, setup):
        # 事務は契約管理の対象外（営業・経理のみ）
        _add_user(db, "office@x.example.com", "office")
        res = client.post(
            "/api/w/contracts", json=_payload(setup),
            headers=_auth(client, "office@x.example.com"),
        )
        assert res.status_code == 403
