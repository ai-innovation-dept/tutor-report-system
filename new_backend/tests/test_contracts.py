"""契約管理 API（/api/w/contracts）のテスト。"""
import calendar
import csv
import io
from datetime import date, timedelta

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


# 期別ケースの基準日。for-tutor の列定義は既定で「現在月」の期を使うため、
# 実行日に依存しないよう「当月」を基準に前期・後期の適用期間を組み立てる。
_TODAY = date.today()
_MONTH_END = date(_TODAY.year, _TODAY.month, calendar.monthrange(_TODAY.year, _TODAY.month)[1])
_PAST = "2020-01-01"
_FUTURE = "2099-12-31"


def _default_cases():
    """既定の期別設定: 前期=過去〜当月末（当月は常に前期）／後期=翌月〜将来。"""
    return [
        {"task_index": 1, "monthly_minutes": 600, "weekly_lessons": 3,
         "start_date": _PAST, "end_date": _MONTH_END.isoformat()},
        {"task_index": 2, "start_date": (_MONTH_END + timedelta(days=1)).isoformat(), "end_date": _FUTURE},
    ]


def _switch_cases(second_starts_today: bool):
    """期の切替が当月の途中にあるケース。

    second_starts_today=True: 後期が今日から始まる（今日＝後期）
    second_starts_today=False: 前期が今日で終わる（今日＝前期）
    いずれも当月は前期・後期の両方に重なるが、列・コマ設定は入力タイミング（今日）の期のみ適用される。
    """
    boundary = _TODAY if second_starts_today else _TODAY + timedelta(days=1)
    return [
        {"task_index": 1, "monthly_minutes": 600, "weekly_lessons": 3,
         "start_date": _PAST, "end_date": (boundary - timedelta(days=1)).isoformat()},
        {"task_index": 2, "start_date": boundary.isoformat(), "end_date": _FUTURE},
    ]


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
        # 担当業務は前期・後期の2本必須（[0]=前期 / [1]=後期）
        "tasks": [
            {"task_name": "数学指導", "task_id": "11111", "contract_id": "99992601"},
            {"task_name": "数学指導（後期）", "task_id": "22222", "contract_id": "99992602"},
        ],
        "workload_cases": _default_cases(),
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
        assert len(body["tasks"]) == 2  # 前期・後期の2本
        assert body["tasks"][0]["task_id"] == "11111"
        assert body["tasks"][1]["task_id"] == "22222"
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

    def test_terms_required(self, client, db, setup):
        # 前期・後期とも委託業務名が必須
        res = client.post("/api/w/contracts", json=_payload(setup, tasks=[]), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422
        # 後期の名称だけ欠けても不可
        one_term = [{"task_name": "数学指導", "task_id": "11111", "contract_id": "99992601"}]
        res = client.post("/api/w/contracts", json=_payload(setup, tasks=one_term), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422
        assert "担当業務（後期）の委託業務名は必須です" in res.json()["detail"]

    def test_term_periods_required(self, client, db, setup):
        # 前期・後期とも適用期間（開始・終了）が必須
        cases = _default_cases()
        cases[1].pop("end_date")
        res = client.post("/api/w/contracts", json=_payload(setup, workload_cases=cases), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422
        assert "担当業務（後期）の適用期間（開始日・終了日）は必須です" in res.json()["detail"]

    def test_term_periods_overlap_rejected(self, client, db, setup):
        # 前期と後期の適用期間の重複は不可
        cases = _default_cases()
        cases[1]["start_date"] = _MONTH_END.isoformat()  # 前期の終了日と同日から後期開始
        res = client.post("/api/w/contracts", json=_payload(setup, workload_cases=cases), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422
        assert "適用期間が重複しています" in res.json()["detail"]

    def test_non_admin_forbidden(self, client, db, setup):
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 403

    def test_dispatch_place_address_roundtrip(self, client, db, setup):
        """派遣先事業所の所在地を契約で登録し、講師向けAPIにも返ること。"""
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, dispatch_place_address="東京都渋谷区〇〇1-2-3"),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 201, res.text
        assert res.json()["dispatch_place_address"] == "東京都渋谷区〇〇1-2-3"
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert entry["dispatch_place_address"] == "東京都渋谷区〇〇1-2-3"
        # PATCHでも更新できる
        contract_id = res.json()["id"]
        patched = client.patch(
            f"/api/w/contracts/{contract_id}",
            json={"dispatch_place_address": "東京都新宿区△△4-5-6"},
            headers=_auth(client, "master@x.example.com"),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["dispatch_place_address"] == "東京都新宿区△△4-5-6"

    def test_work_location_roundtrip(self, client, db, setup):
        """就業場所を契約で登録し、講師向けAPIにも返ること（報告書の所在地の下に表示する項目）。"""
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, work_location="〇〇高等学校 △△校舎"),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 201, res.text
        assert res.json()["work_location"] == "〇〇高等学校 △△校舎"
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert entry["work_location"] == "〇〇高等学校 △△校舎"
        # PATCHでも更新できる
        contract_id = res.json()["id"]
        patched = client.patch(
            f"/api/w/contracts/{contract_id}",
            json={"work_location": "□□中学校 本校舎"},
            headers=_auth(client, "master@x.example.com"),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["work_location"] == "□□中学校 本校舎"


class TestContractWorkloadCases:
    """前期・後期の期別設定（月時間（分）・週コマ・適用期間）。"""

    CASES = [
        {"task_index": 1, "monthly_minutes": 3000, "weekly_lessons": 15, "start_date": "2026-04-01", "end_date": "2026-08-31"},
        {"task_index": 2, "monthly_minutes": 4000, "weekly_lessons": 20, "start_date": "2026-09-01", "end_date": "2027-03-31"},
    ]

    def test_create_with_term_cases(self, client, db, setup):
        payload = _payload(setup, monthly_minutes=None, weekly_lessons=None, workload_cases=self.CASES)
        res = client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        body = res.json()
        assert [c["task_index"] for c in body["workload_cases"]] == [1, 2]
        assert body["workload_cases"][0]["monthly_minutes"] == 3000
        assert body["workload_cases"][1]["start_date"] == "2026-09-01"

    def test_single_values_no_longer_become_case(self, client, db, setup):
        """旧形式の単一値（monthly_minutes / weekly_lessons）はケースへ合成しない（期別設定のみ保存）。"""
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        cases = res.json()["workload_cases"]
        assert [c["task_index"] for c in cases] == [1, 2]
        assert cases[0]["monthly_minutes"] == 600  # _default_cases の前期の値（単一値600とは別管理）
        assert cases[1]["monthly_minutes"] is None

    def test_case_task_index_roundtrip(self, client, db, setup):
        """期別ケース（task_index付き）が保存・返却されること。範囲外は422。"""
        payload = _payload(setup, monthly_minutes=None, weekly_lessons=None, workload_cases=self.CASES)
        res = client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        saved = res.json()["workload_cases"]
        assert [c["task_index"] for c in saved] == [1, 2]
        assert saved[0]["monthly_minutes"] == 3000
        # 講師向けAPIにも task_index 付きで返る
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert [c["task_index"] for c in entry["workload_cases"]] == [1, 2]
        # task_index は1（前期）・2（後期）のみ
        bad_case = dict(self.CASES[0], task_index=3)
        bad = _payload(setup, workload_cases=[bad_case, self.CASES[1]])
        assert client.post("/api/w/contracts", json=bad, headers=_auth(client, "master@x.example.com")).status_code == 422

    def test_patch_replaces_cases(self, client, db, setup):
        headers = _auth(client, "master@x.example.com")
        created = client.post("/api/w/contracts", json=_payload(setup), headers=headers).json()
        res = client.patch(f"/api/w/contracts/{created['id']}", json={"workload_cases": self.CASES}, headers=headers)
        assert res.status_code == 200, res.text
        assert len(res.json()["workload_cases"]) == 2

    def test_patch_terms_validated_on_final_state(self, client, db, setup):
        """期別設定を更新するPATCHは最終状態で前期・後期の必須（適用期間）を検証する。"""
        headers = _auth(client, "master@x.example.com")
        created = client.post("/api/w/contracts", json=_payload(setup), headers=headers).json()
        # 後期のケースを落とす（前期のみ）→ 422
        res = client.patch(f"/api/w/contracts/{created['id']}", json={"workload_cases": [self.CASES[0]]}, headers=headers)
        assert res.status_code == 422
        assert "担当業務（後期）の適用期間" in res.json()["detail"]
        # 拒否後は契約が変わっていない
        body = client.get(f"/api/w/contracts/{created['id']}", headers=headers).json()
        assert len(body["workload_cases"]) == 2

    def test_patch_other_fields_ok_on_legacy_contract(self, client, db, setup):
        """旧形式（担当業務①のみ・期間なしケース）の契約でも、他フィールドのみの部分更新は通る。"""
        assignment = Assignment(tutor_id=setup["tutor"].id, parent_id=setup["school"].id, student_name="-", system_type="new")
        db.add(assignment)
        db.flush()
        profile = WorkAssignmentProfile(
            assignment_id=assignment.id, tutor_id=setup["tutor"].id, school_id=setup["school"].id,
            form_type="monthly_dispatch", task_name_1="数学指導", workload_cases=[{"monthly_minutes": 600}],
        )
        db.add(profile)
        db.commit()
        headers = _auth(client, "master@x.example.com")
        res = client.patch(f"/api/w/contracts/{profile.id}", json={"our_staff": "新担当"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["our_staff"] == "新担当"
        # 期別設定に触るPATCHは新仕様の検証がかかる（設定する期＝前期の適用期間が無いケースは不可）。
        # ※前期のみ＋適用期間ありへの更新は 202607170952（少なくとも1期）で合法になった。
        res = client.patch(
            f"/api/w/contracts/{profile.id}",
            json={"workload_cases": [{"task_index": 1, "monthly_minutes": 600}]}, headers=headers,
        )
        assert res.status_code == 422
        assert "担当業務（前期）の適用期間" in res.json()["detail"]
        # 前期のみ＋適用期間ありなら通る（両期必須の緩和）
        res = client.patch(f"/api/w/contracts/{profile.id}", json={"workload_cases": [self.CASES[0]]}, headers=headers)
        assert res.status_code == 200, res.text

    def test_invalid_case_period_rejected(self, client, db, setup):
        cases = _default_cases()
        cases[0]["start_date"], cases[0]["end_date"] = "2026-09-01", "2026-03-31"  # 終了日が開始日より前
        payload = _payload(setup, workload_cases=cases)
        res = client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 422

    def test_for_tutor_returns_cases(self, client, db, setup):
        payload = _payload(setup, monthly_minutes=None, weekly_lessons=None, workload_cases=self.CASES)
        assert client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@x.example.com")).status_code == 201
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 200
        entry = res.json()[0]
        assert len(entry["workload_cases"]) == 2
        assert entry["workload_cases"][0]["weekly_lessons"] == 15


class TestContractForTutor:
    def _create(self, client, setup, **ov):
        return client.post("/api/w/contracts", json=_payload(setup, **ov), headers=_auth(client, "master@x.example.com")).json()

    def test_for_tutor_returns_column_definition(self, client, db, setup):
        # 委託業務（分のみ）＋採点専用欄を有効化 → 末尾に採点（回）の1列(count_minutes)。
        # 担当業務は対象月（既定=当月）の期のみ（_default_cases では当月=前期）。
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
        # 固定先頭 → 当月の期の担当業務（前期のみ）→ 採点(回＋分=1列) → 固定末尾 の順
        assert keys == [
            "date", "start", "end", "subject_period",
            "task_minutes_1",
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

    def test_for_tutor_scoring_custom_label_unit(self, client, db, setup):
        # 項目名・単位を任意指定 → 見出し・単位・分見出しに反映（分は固定）
        self._create(
            client, setup,
            scoring_enabled=True,
            scoring_label="進路相談",
            scoring_unit="人",
            scoring_task_id="S1",
            scoring_contract_id="SC1",
        )
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        scoring = next(c for c in entry["column_definition"] if c["key"] == "scoring")
        assert scoring["label"] == "進路相談（人）"
        assert scoring["minutes_label"] == "進路相談（分）"
        assert scoring["unit"] == "人"
        assert scoring["count_key"] == "scoring_count"
        assert scoring["minutes_key"] == "scoring_minutes"

    def test_for_tutor_scoring_defaults_when_unset(self, client, db, setup):
        # 項目名・単位を未指定なら既定（採点／回）へフォールバック（後方互換）
        self._create(client, setup, scoring_enabled=True)
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        scoring = next(c for c in entry["column_definition"] if c["key"] == "scoring")
        assert scoring["label"] == "採点（回）"
        assert scoring["unit"] == "回"

    def test_for_tutor_without_scoring_omits_scoring_column(self, client, db, setup):
        # 採点無効（デフォルト）なら採点列は生成されない
        self._create(client, setup)
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        keys = [c["key"] for c in entry["column_definition"]]
        assert "scoring" not in keys
        assert "task_minutes_1" in keys

    def test_for_tutor_term_columns_by_month(self, client, db, setup):
        """列定義は対象月の報告書に適用する期の担当業務**1列のみ**を含む。

        基準日＝今日を対象月内へクランプした日（過去月は月末・未来月は月初時点の期）。
        """
        self._create(
            client, setup,
            tasks=[
                {"task_name": "数学指導", "task_id": "T1", "contract_id": "C1"},
                {"task_name": "数学指導（後期）", "task_id": "T2", "contract_id": "C2"},
            ],
            workload_cases=[
                {"task_index": 1, "monthly_minutes": 3000, "weekly_lessons": 15, "start_date": "2026-04-01", "end_date": "2026-08-31"},
                {"task_index": 2, "monthly_minutes": 6000, "weekly_lessons": 20, "start_date": "2026-09-01", "end_date": "2027-03-31"},
            ],
        )
        headers = _auth(client, "tutor@x.example.com")

        def keys_for(month):
            entry = client.get(f"/api/w/contracts/for-tutor?target_month={month}", headers=headers).json()[0]
            return [c["key"] for c in entry["column_definition"] if str(c["key"]).startswith("task_minutes_")]

        assert keys_for("2026-06") == ["task_minutes_1"]  # 前期の月
        assert keys_for("2026-10") == ["task_minutes_2"]  # 後期の月
        # どの期にも該当しない月は入力不能にならないよう全担当業務へフォールバック
        assert keys_for("2027-06") == ["task_minutes_1", "task_minutes_2"]

    def test_for_tutor_switch_month_uses_term_at_today(self, client, db, setup):
        """期の切替が月の途中にある月でも、入力タイミング（今日）の期の1列のみ返す。"""
        tasks = [
            {"task_name": "数学指導", "task_id": "T1", "contract_id": "C1"},
            {"task_name": "数学指導（後期）", "task_id": "T2", "contract_id": "C2"},
        ]
        headers = _auth(client, "tutor@x.example.com")
        admin = _auth(client, "master@x.example.com")

        def keys_now():
            entry = client.get("/api/w/contracts/for-tutor", headers=headers).json()[0]
            return [c["key"] for c in entry["column_definition"] if str(c["key"]).startswith("task_minutes_")]

        # 今日から後期 → 後期の1列
        created = client.post(
            "/api/w/contracts", json=_payload(setup, tasks=tasks, workload_cases=_switch_cases(second_starts_today=True)),
            headers=admin,
        )
        assert created.status_code == 201, created.text
        assert keys_now() == ["task_minutes_2"]
        # 今日まで前期（後期は明日から）へ更新 → 前期の1列
        patched = client.patch(
            f"/api/w/contracts/{created.json()['id']}",
            json={"tasks": tasks, "workload_cases": _switch_cases(second_starts_today=False)},
            headers=admin,
        )
        assert patched.status_code == 200, patched.text
        assert keys_now() == ["task_minutes_1"]

    def test_for_tutor_term_slots_roundtrip(self, client, db, setup):
        """期別のコマ設定（workload_cases[].slots）が講師向けAPIへ返ること。"""
        cases = _default_cases()
        cases[0]["slots"] = [{"start": "08:30", "end": "09:20"}, {"start": "09:30", "end": "10:20"}]
        cases[1]["slots"] = [{"start": "13:00", "end": "13:50"}]
        self._create(client, setup, workload_cases=cases)
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert entry["workload_cases"][0]["slots"] == [{"start": "08:30", "end": "09:20"}, {"start": "09:30", "end": "10:20"}]
        assert entry["workload_cases"][1]["slots"] == [{"start": "13:00", "end": "13:50"}]

    def test_for_tutor_only_own_contracts(self, client, db, setup):
        self._create(client, setup)
        other_tutor = _add_user(db, "tutor2@x.example.com", "tutor")
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor2@x.example.com"))
        assert res.status_code == 200
        assert res.json() == []

    def test_for_tutor_requires_tutor_role(self, client, db, setup):
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 403


class TestMainSubTasks:
    """委託業務の担当業務（前期・後期の2本必須）／サブ業務（①〜⑤・任意）分割。"""

    MAIN = [
        {"task_name": "a", "task_id": "a", "contract_id": "a"},
        {"task_name": "b", "task_id": "b", "contract_id": "b"},
    ]
    SUB = [
        {"task_name": "c", "task_id": "c", "contract_id": "c"},
        {"task_name": "d", "task_id": "d", "contract_id": "d"},
        {"task_name": "e", "task_id": "e", "contract_id": "e"},
    ]

    def _create(self, client, setup, **ov):
        return client.post("/api/w/contracts", json=_payload(setup, **ov), headers=_auth(client, "master@x.example.com"))

    def test_create_with_main_and_sub(self, client, db, setup):
        res = self._create(client, setup, tasks=self.MAIN, sub_tasks=self.SUB)
        assert res.status_code == 201, res.text
        body = res.json()
        assert [t["task_name"] for t in body["tasks"]] == ["a", "b"]
        assert [t["task_name"] for t in body["sub_tasks"]] == ["c", "d", "e"]

    def test_column_order_main_then_sub(self, client, db, setup):
        """報告書の列は担当（適用期の1列）→サブ①②③の順（例: 回数,日付,時間,担当時限,a,c,d,e,後は同じ）。

        既定ケースでは今日＝前期のため、担当業務は前期（a）の1列のみ。
        """
        assert self._create(client, setup, tasks=self.MAIN, sub_tasks=self.SUB).status_code == 201
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        keys = [c["key"] for c in entry["column_definition"]]
        assert keys == [
            "date", "start", "end", "subject_period",
            "task_minutes_1",
            "sub_minutes_1", "sub_minutes_2", "sub_minutes_3",
            "break_minutes", "commute_fee", "note",
        ]
        labels = [c["label"] for c in entry["column_definition"][4:8]]
        assert labels == ["a（分）", "c（分）", "d（分）", "e（分）"]
        sub1 = next(c for c in entry["column_definition"] if c["key"] == "sub_minutes_1")
        assert sub1["task_id"] == "c"
        assert sub1["contract_id"] == "c"
        assert sub1["summable"] is True

    def test_main_over_limit_rejected(self, client, db, setup):
        # 担当業務は前期・後期の2件固定（3件以上は不可）
        over = [{"task_name": f"m{i}"} for i in range(3)]
        assert self._create(client, setup, tasks=over).status_code == 422

    def test_sub_over_limit_rejected(self, client, db, setup):
        over = [{"task_name": f"s{i}"} for i in range(6)]
        assert self._create(client, setup, sub_tasks=over).status_code == 422

    def test_sub_optional(self, client, db, setup):
        res = self._create(client, setup)
        assert res.status_code == 201, res.text
        assert res.json()["sub_tasks"] == []

    def test_patch_sub_tasks(self, client, db, setup):
        created = self._create(client, setup).json()
        headers = _auth(client, "master@x.example.com")
        res = client.patch(f"/api/w/contracts/{created['id']}", json={"sub_tasks": self.SUB}, headers=headers)
        assert res.status_code == 200, res.text
        assert len(res.json()["sub_tasks"]) == 3
        # サブを空に更新するとクリアされる
        res = client.patch(f"/api/w/contracts/{created['id']}", json={"sub_tasks": []}, headers=headers)
        assert res.json()["sub_tasks"] == []


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

    def test_delete_hard_removes_contract(self, client, db, setup):
        # hard=true は物理削除：行が消え、一覧からも消える
        created = self._create(client, setup)
        headers = _auth(client, "master@x.example.com")
        res = client.delete(f"/api/w/contracts/{created['id']}?hard=true", headers=headers)
        assert res.status_code == 200
        db.expire_all()
        assert db.get(WorkAssignmentProfile, __import__("uuid").UUID(created["id"])) is None
        listed = client.get("/api/w/contracts", headers=headers).json()
        assert created["id"] not in [c["id"] for c in listed]


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cis.headers())
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in cis.headers()})
    return buf.getvalue().encode("utf-8-sig")


def _csv_row(tutor_no, school_no="S100", **over):
    row = {
        cis.TUTOR_NO: tutor_no,
        cis.SCHOOL_NO: school_no,
        cis.CUSTOMER_ID: "9999",
        # 担当業務は前期・後期の2本（名称・適用期間が必須）
        cis._main_name_h(1): "数学指導",
        cis._main_id_h(1): "T1",
        cis._monthly_minutes_h(1): "600",
        cis._weekly_lessons_h(1): "3",
        cis._case_start_h(1): "2026-04-01",
        cis._case_end_h(1): "2026-08-31",
        cis._main_name_h(2): "数学指導（後期）",
        cis._case_start_h(2): "2026-09-01",
        cis._case_end_h(2): "2027-03-31",
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
        school.user_no = "S100"
        school.display_name = "渋谷高校"
        db.commit()
        return {**setup, "imp_tutor": tutor, "imp_school": school}

    def _upload(self, client, data: bytes):
        return client.post(
            "/api/w/contracts/import",
            files={"file": ("contracts.csv", data, "text/csv")},
            headers=_auth(client, "master@x.example.com"),
        )

    def test_export_empty_has_header(self, client, setup):
        res = client.get("/api/w/contracts/export", headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 200
        assert "text/csv" in res.headers["content-type"]
        header = res.content.decode("utf-8-sig").splitlines()[0]
        assert cis.TUTOR_NO in header and cis.SCHOOL_NO in header and cis.CLASSROOM_NAME in header
        # 前期・後期の期別列（名称・月時間・週コマ・適用期間）を持つ
        for i in (1, 2):
            for h in (cis._main_name_h(i), cis._monthly_minutes_h(i), cis._weekly_lessons_h(i),
                      cis._case_start_h(i), cis._case_end_h(i)):
                assert h in header, h

    def test_export_includes_registered_contract(self, client, db, import_setup):
        self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "佐藤"})]))
        res = client.get("/api/w/contracts/export", headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 200, res.text
        text = res.content.decode("utf-8-sig")
        # 番号＋参考の氏名・学校名が入った状態で出力される
        assert "T100" in text and "S100" in text
        assert "山田太郎" in text and "渋谷高校" in text

    def test_csv_upsert_preserves_display_flags(self, client, db, import_setup):
        # ドロワーで休憩時間を非表示にした契約を、CSV再取込しても表示フラグは保持される
        self._upload(client, _csv_bytes([_csv_row("T100", "S100")]))
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        profile.show_break_minutes = False
        db.commit()
        self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "更新後"})]))
        db.refresh(profile)
        assert profile.our_staff == "更新後"
        assert profile.show_break_minutes is False  # CSV取込で表示フラグは保持

    def test_csv_upsert_preserves_period_slots(self, client, db, import_setup):
        # ドロワーで設定したコマ設定は、CSV再取込しても保持される（CSVはコマ設定を扱わない）
        self._upload(client, _csv_bytes([_csv_row("T100", "S100")]))
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        profile.period_slots = [{"start": "08:30", "end": "09:20"}]
        db.commit()
        self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "更新後"})]))
        db.refresh(profile)
        assert profile.our_staff == "更新後"
        assert profile.period_slots == [{"start": "08:30", "end": "09:20"}]

    def test_csv_upsert_preserves_use_period_slots(self, client, db, import_setup):
        # コマ設定の使用/未使用（use_period_slots）もCSVでは扱わないため、再取込で保持される
        self._upload(client, _csv_bytes([_csv_row("T100", "S100")]))
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        profile.use_period_slots = False
        db.commit()
        self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "更新後"})]))
        db.refresh(profile)
        assert profile.our_staff == "更新後"
        assert profile.use_period_slots is False  # CSV取込で使用/未使用は保持

    def test_import_requires_school_no(self, client, db, import_setup):
        row = _csv_row("T100", "")  # 学校番号なし
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 400
        assert db.query(WorkAssignmentProfile).count() == 0

    def test_import_creates_then_upserts(self, client, db, import_setup):
        # 新規取り込み
        res = self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "旧担当"})]))
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 1, "created": 1, "updated": 0}
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.our_staff == "旧担当"
        assert profile.task_name_1 == "数学指導"
        # 同一(講師×学校)を再取り込み → upsertで更新
        res2 = self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "新担当"})]))
        assert res2.status_code == 200, res2.text
        assert res2.json() == {"imported": 1, "created": 0, "updated": 1}
        db.refresh(profile)
        assert profile.our_staff == "新担当"
        # 前期・後期の名称＋期別設定（月時間・週コマ・適用期間）も取り込まれている
        assert profile.task_name_2 == "数学指導（後期）"
        cases = {c["task_index"]: c for c in profile.workload_cases}
        assert cases[1]["monthly_minutes"] == 600
        assert cases[1]["weekly_lessons"] == 3
        assert cases[1]["start_date"] == "2026-04-01"
        assert cases[2]["start_date"] == "2026-09-01"
        assert cases[2]["end_date"] == "2027-03-31"

    def test_parse_date_accepts_excel_variants(self):
        # Excelが「2026/6/1」(スラッシュ・ゼロ詰めなし)等へ変換しても受理する
        from datetime import date as _date
        for text in ("2026-06-01", "2026/06/01", "2026/6/1", "2026-6-1", "2026.6.1"):
            errs: list = []
            assert cis._parse_date(text, "x", errs) == _date(2026, 6, 1), text
            assert errs == [], text
        bad: list = []
        assert cis._parse_date("not-a-date", "x", bad) is None
        assert bad  # 不正値はエラーを記録する

    def test_import_accepts_excel_slash_dates(self, client, db, import_setup):
        # CSVエクスポート→Excel編集で日付が「2026/6/1」化しても取り込める（バグ修正の回帰テスト）
        from datetime import date as _date
        row = _csv_row("T100", "S100", **{cis.CONTRACT_START: "2026/6/1", cis.CONTRACT_END: "2026/8/1"})
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.contract_start == _date(2026, 6, 1)
        assert profile.contract_end == _date(2026, 8, 1)

    def test_import_scoring_enabled(self, client, db, import_setup):
        res = self._upload(client, _csv_bytes([
            _csv_row("T100", "S100", **{cis.SCORING_ENABLED: "有", cis.SCORING_TASK_ID: "S1"})]))
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.scoring_enabled is True
        assert profile.scoring_task_id == "S1"

    def test_import_scoring_label_unit(self, client, db, import_setup):
        res = self._upload(client, _csv_bytes([
            _csv_row("T100", "S100", **{cis.SCORING_ENABLED: "有", cis.SCORING_LABEL: "進路相談", cis.SCORING_UNIT: "人"})]))
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.scoring_enabled is True
        assert profile.scoring_label == "進路相談"
        assert profile.scoring_unit == "人"

    def test_import_all_or_nothing(self, client, db, import_setup):
        # 1行目は有効、2行目は講師番号が不正 → 全件中止（何も登録しない）
        data = _csv_bytes([
            _csv_row("T100", "S100"),
            _csv_row("UNKNOWN", "S100"),
        ])
        res = self._upload(client, data)
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert any("UNKNOWN" in e for e in detail["errors"])
        assert db.query(WorkAssignmentProfile).count() == 0

    def test_import_skips_example_row(self, client, db, import_setup):
        # 講師番号が#始まりの記入例行はスキップ、有効行のみ取り込む
        data = _csv_bytes([
            _csv_row("#T0001", "S100"),
            _csv_row("T100", "S100"),
        ])
        res = self._upload(client, data)
        assert res.status_code == 200, res.text
        assert res.json()["imported"] == 1

    def test_import_first_task_required(self, client, db, import_setup):
        row = _csv_row("T100", "S100")
        row[cis._main_name_h(1)] = ""
        row[cis._main_id_h(1)] = ""
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 400
        assert db.query(WorkAssignmentProfile).count() == 0

    def test_import_term_periods_required(self, client, db, import_setup):
        # 後期の適用期間が欠けた行はエラー（全件中止）
        row = _csv_row("T100", "S100")
        row[cis._case_start_h(2)] = ""
        row[cis._case_end_h(2)] = ""
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert any("担当業務（後期）の適用期間" in e for e in detail["errors"])
        assert db.query(WorkAssignmentProfile).count() == 0

    def test_import_term_period_overlap_rejected(self, client, db, import_setup):
        # 前期と後期の適用期間が重複する行はエラー
        row = _csv_row("T100", "S100", **{cis._case_start_h(2): "2026-08-01"})
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 400
        detail = res.json()["detail"]
        assert any("適用期間が重複しています" in e for e in detail["errors"])

    def test_export_import_roundtrip_terms(self, client, db, import_setup):
        # 取込→エクスポート→再取込で期別設定（前期・後期）が保たれる
        self._upload(client, _csv_bytes([_csv_row("T100", "S100")]))
        exported = client.get("/api/w/contracts/export", headers=_auth(client, "master@x.example.com"))
        assert exported.status_code == 200
        res = self._upload(client, exported.content)
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 1, "created": 0, "updated": 1}
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        db.refresh(profile)
        cases = {c["task_index"]: c for c in profile.workload_cases}
        assert cases[1]["monthly_minutes"] == 600
        assert cases[2]["start_date"] == "2026-09-01"

    def test_csv_upsert_preserves_term_slots(self, client, db, import_setup):
        # 画面で設定した期別コマ設定は、CSV再取込しても task_index で引き継がれる（CSVはコマ設定を扱わない）
        self._upload(client, _csv_bytes([_csv_row("T100", "S100")]))
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        cases = [dict(c) for c in profile.workload_cases]
        for case in cases:
            if case["task_index"] == 1:
                case["slots"] = [{"start": "08:30", "end": "09:20"}]
        profile.workload_cases = cases
        db.commit()
        self._upload(client, _csv_bytes([_csv_row("T100", "S100", **{cis.OUR_STAFF: "更新後"})]))
        db.refresh(profile)
        assert profile.our_staff == "更新後"
        slots_by_index = {c["task_index"]: c.get("slots") or [] for c in profile.workload_cases}
        assert slots_by_index[1] == [{"start": "08:30", "end": "09:20"}]
        assert slots_by_index[2] == []

    def test_import_dispatch_address_and_schedule(self, client, db, import_setup):
        # 所在地・就業場所・スケジュール欄（旧シフト指定欄）のCSV取り込み
        row = _csv_row("T100", "S100", **{
            cis.DISPATCH_ADDRESS: "東京都渋谷区〇〇1-2-3",
            cis.WORK_LOCATION: "〇〇高等学校 △△校舎",
            cis.SHIFT_NOTE: "月(9:30～10:20)金(11:30～12:20)",
        })
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.dispatch_place_address == "東京都渋谷区〇〇1-2-3"
        assert profile.work_location == "〇〇高等学校 △△校舎"
        assert profile.shift_note == "月(9:30～10:20)金(11:30～12:20)"

    def test_import_sub_tasks(self, client, db, import_setup):
        # サブ業務列の取り込み（メインに加えてサブ①②を登録）
        row = _csv_row("T100", "S100", **{
            cis._sub_name_h(1): "教科会",
            cis._sub_id_h(1): "S1",
            cis._sub_contract_id_h(1): "SC1",
            cis._sub_name_h(2): "採点補助",
        })
        res = self._upload(client, _csv_bytes([row]))
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == import_setup["imp_tutor"].id))
        assert profile.task_name_1 == "数学指導"
        assert profile.sub_task_name_1 == "教科会"
        assert profile.sub_task_id_1 == "S1"
        assert profile.sub_contract_id_1 == "SC1"
        assert profile.sub_task_name_2 == "採点補助"

    def test_import_non_admin_forbidden(self, client, import_setup):
        res = client.post(
            "/api/w/contracts/import",
            files={"file": ("c.csv", _csv_bytes([_csv_row("T100", "S100")]), "text/csv")},
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

    def test_tutor_forbidden_from_contracts(self, client, db, setup):
        # 講師は契約管理の対象外（経理・管理責任者・営業・事務のみ）
        res = client.post(
            "/api/w/contracts", json=_payload(setup),
            headers=_auth(client, "tutor@x.example.com"),
        )
        assert res.status_code == 403


class TestOfficeContractAccess:
    """事務ロールも契約管理を営業・経理と同等に利用できる。"""

    def test_office_can_create_and_list_contracts(self, client, db, setup):
        _add_user(db, "office@x.example.com", "office")
        created = client.post(
            "/api/w/contracts", json=_payload(setup),
            headers=_auth(client, "office@x.example.com"),
        )
        assert created.status_code == 201, created.text
        listed = client.get("/api/w/contracts", headers=_auth(client, "office@x.example.com"))
        assert listed.status_code == 200
        assert len(listed.json()) == 1


class TestChiefContractAccess:
    """管理責任者はナビに契約管理リンクを持つため、APIも利用できる。"""

    def test_chief_can_create_and_list_contracts(self, client, db, setup):
        _add_user(db, "chief@x.example.com", "admin_chief")
        created = client.post(
            "/api/w/contracts", json=_payload(setup),
            headers=_auth(client, "chief@x.example.com"),
        )
        assert created.status_code == 201, created.text
        listed = client.get("/api/w/contracts", headers=_auth(client, "chief@x.example.com"))
        assert listed.status_code == 200
        assert len(listed.json()) == 1


class TestClassroomAndDisplayFlags:
    """教室名＋報告書の表示項目フラグ（既定は全て表示）。"""

    def test_defaults_all_shown_and_no_classroom(self, client, db, setup):
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["classroom_name"] is None
        for flag in ("show_dispatch_address", "show_work_content", "show_commuter_pass",
                     "show_break_minutes", "show_schedule_note"):
            assert body[flag] is True

    def test_create_with_classroom_and_flags(self, client, db, setup):
        payload = _payload(
            setup,
            classroom_name="3年A組教室",
            show_commuter_pass=False,
            show_break_minutes=False,
        )
        res = client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["classroom_name"] == "3年A組教室"
        assert body["show_commuter_pass"] is False
        assert body["show_break_minutes"] is False
        assert body["show_dispatch_address"] is True  # 指定外は既定の表示

    def test_for_tutor_returns_classroom_and_flags(self, client, db, setup):
        client.post(
            "/api/w/contracts",
            json=_payload(setup, classroom_name="渋谷教室", show_schedule_note=False),
            headers=_auth(client, "master@x.example.com"),
        )
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 200, res.text
        items = res.json()
        assert len(items) == 1
        assert items[0]["classroom_name"] == "渋谷教室"
        assert items[0]["show_schedule_note"] is False
        assert items[0]["show_dispatch_address"] is True

    def test_update_flags(self, client, db, setup):
        created = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "master@x.example.com"))
        cid = created.json()["id"]
        res = client.patch(
            f"/api/w/contracts/{cid}",
            json={"show_work_content": False, "classroom_name": "新教室"},
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["show_work_content"] is False
        assert body["classroom_name"] == "新教室"


class TestPeriodSlots:
    """コマ設定（担当時限の時間割）。設定がある契約は講師フォームで時間割から自動計算する。"""

    SLOTS = [
        {"start": "08:30", "end": "09:20"},
        {"start": "09:30", "end": "10:20"},
        {"start": "10:30", "end": "11:20"},
        {"start": "11:30", "end": "12:20"},
    ]

    def test_create_with_period_slots(self, client, db, setup):
        res = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=self.SLOTS),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 201, res.text
        assert res.json()["period_slots"] == self.SLOTS

    def test_default_empty(self, client, db, setup):
        res = client.post("/api/w/contracts", json=_payload(setup), headers=_auth(client, "master@x.example.com"))
        assert res.status_code == 201, res.text
        assert res.json()["period_slots"] == []

    def test_for_tutor_returns_period_slots(self, client, db, setup):
        client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=self.SLOTS),
            headers=_auth(client, "master@x.example.com"),
        )
        res = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com"))
        assert res.status_code == 200, res.text
        assert res.json()[0]["period_slots"] == self.SLOTS

    def test_start_must_be_before_end(self, client, db, setup):
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, period_slots=[{"start": "09:20", "end": "08:30"}]),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_overlap_rejected(self, client, db, setup):
        slots = [{"start": "08:30", "end": "09:20"}, {"start": "09:10", "end": "10:00"}]
        res = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=slots),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_slots_number_order_independent_and_sorted_on_save(self, client, db, setup):
        # ⑤に①〜④より早い朝の時間帯を設定できる（入力順は時間順でなくてもよい）。
        # 保存時に開始時刻順へ自動で並べ替えられ、①が最も早い時間帯になる。
        slots = self.SLOTS + [{"start": "07:30", "end": "08:20"}]
        sorted_slots = [{"start": "07:30", "end": "08:20"}] + self.SLOTS
        res = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=slots),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 201, res.text
        assert res.json()["period_slots"] == sorted_slots
        # 期別コマ設定（workload_cases[].slots）でも同様に並べ替えて保存される
        cases = _default_cases()
        cases[1]["slots"] = slots
        patched = client.patch(
            f"/api/w/contracts/{res.json()['id']}", json={"workload_cases": cases},
            headers=_auth(client, "master@x.example.com"),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["workload_cases"][1]["slots"] == sorted_slots
        # 講師向けAPI・DB保存値とも並べ替え済み
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert entry["workload_cases"][1]["slots"] == sorted_slots

    def test_slots_nonadjacent_overlap_rejected(self, client, db, setup):
        # 直前のコマとは重ならなくても、離れた番号のコマと重なる時間帯は不可（⑤が①と重なる例）
        slots = self.SLOTS + [{"start": "08:00", "end": "08:40"}]
        res = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=slots),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422
        assert "コマ5がコマ1と時間が重なっています" in res.text

    def test_max_10_slots(self, client, db, setup):
        slots = [{"start": f"{8 + i:02d}:00", "end": f"{8 + i:02d}:50"} for i in range(11)]
        res = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=slots),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_time_format_validated(self, client, db, setup):
        # HH:MM 形式（ゼロ詰め2桁）のみ受理する
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, period_slots=[{"start": "8:30", "end": "09:20"}]),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_break_hidden_conflicts_with_slots(self, client, db, setup):
        # 休憩時間を非表示にすると「隙間→休憩」の自動計算が成立しないため併用不可
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, period_slots=self.SLOTS, show_break_minutes=False),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_break_hidden_conflicts_with_term_slots(self, client, db, setup):
        # 期別コマ設定（workload_cases[].slots）でも休憩非表示との併用は不可
        cases = _default_cases()
        cases[0]["slots"] = self.SLOTS
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, workload_cases=cases, show_break_minutes=False),
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_patch_break_hidden_rejected_when_term_slots_exist(self, client, db, setup):
        cases = _default_cases()
        cases[1]["slots"] = self.SLOTS
        created = client.post(
            "/api/w/contracts", json=_payload(setup, workload_cases=cases),
            headers=_auth(client, "master@x.example.com"),
        )
        assert created.status_code == 201, created.text
        res = client.patch(
            f"/api/w/contracts/{created.json()['id']}", json={"show_break_minutes": False},
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422

    def test_patch_break_hidden_rejected_when_slots_exist(self, client, db, setup):
        created = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=self.SLOTS),
            headers=_auth(client, "master@x.example.com"),
        )
        cid = created.json()["id"]
        res = client.patch(
            f"/api/w/contracts/{cid}", json={"show_break_minutes": False},
            headers=_auth(client, "master@x.example.com"),
        )
        assert res.status_code == 422
        # 拒否後は契約が変更されていない
        body = client.get(f"/api/w/contracts/{cid}", headers=_auth(client, "master@x.example.com")).json()
        assert body["show_break_minutes"] is True
        assert body["period_slots"] == self.SLOTS

    def test_patch_updates_preserves_and_clears_slots(self, client, db, setup):
        created = client.post(
            "/api/w/contracts", json=_payload(setup, period_slots=self.SLOTS),
            headers=_auth(client, "master@x.example.com"),
        )
        cid = created.json()["id"]
        headers = _auth(client, "master@x.example.com")
        # 更新
        res = client.patch(
            f"/api/w/contracts/{cid}",
            json={"period_slots": [{"start": "13:00", "end": "13:50"}]}, headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["period_slots"] == [{"start": "13:00", "end": "13:50"}]
        # period_slots キーを送らないPATCHでは保持される
        res2 = client.patch(f"/api/w/contracts/{cid}", json={"our_staff": "変更"}, headers=headers)
        assert res2.status_code == 200, res2.text
        assert res2.json()["period_slots"] == [{"start": "13:00", "end": "13:50"}]
        # 空リストでクリア
        res3 = client.patch(f"/api/w/contracts/{cid}", json={"period_slots": []}, headers=headers)
        assert res3.status_code == 200, res3.text
        assert res3.json()["period_slots"] == []


class TestUsePeriodSlots:
    """コマ設定の使用/未使用（use_period_slots・202607170831）。

    未使用の契約はコマ設定を保持したまま自動計算を無効化し、講師フォームは
    担当時限列なしの手入力方式（開始・各分を手入力→終了のみ自動計算）になる。
    """

    SLOTS = [{"start": "08:30", "end": "09:20"}, {"start": "09:30", "end": "10:20"}]

    def _headers(self, client):
        return _auth(client, "master@x.example.com")

    def test_default_true_and_patch_roundtrip(self, client, db, setup):
        headers = self._headers(client)
        created = client.post("/api/w/contracts", json=_payload(setup), headers=headers)
        assert created.status_code == 201, created.text
        assert created.json()["use_period_slots"] is True  # 既定は使用（従来動作）
        cid = created.json()["id"]
        res = client.patch(f"/api/w/contracts/{cid}", json={"use_period_slots": False}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["use_period_slots"] is False
        assert client.get(f"/api/w/contracts/{cid}", headers=headers).json()["use_period_slots"] is False

    def test_unused_keeps_stored_slots(self, client, db, setup):
        # 未使用へ切り替えてもコマ設定の値は保持される（グレイアウト＝編集不可・値は残す）
        headers = self._headers(client)
        cases = _default_cases()
        cases[0]["slots"] = self.SLOTS
        created = client.post("/api/w/contracts", json=_payload(setup, workload_cases=cases), headers=headers)
        assert created.status_code == 201, created.text
        res = client.patch(f"/api/w/contracts/{created.json()['id']}", json={"use_period_slots": False}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["use_period_slots"] is False
        assert res.json()["workload_cases"][0]["slots"] == self.SLOTS

    def test_column_definition_omits_subject_period_when_unused(self, client, db, setup):
        # 未使用の契約は報告書の列定義に担当時限列を生成しない＝講師フォームは手入力方式
        headers = self._headers(client)
        assert client.post(
            "/api/w/contracts", json=_payload(setup, use_period_slots=False), headers=headers,
        ).status_code == 201
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert entry["use_period_slots"] is False
        keys = [c["key"] for c in entry["column_definition"]]
        assert "subject_period" not in keys
        # 開始・終了・分列・末尾固定列は従来どおり
        assert keys == [
            "date", "start", "end",
            "task_minutes_1",
            "break_minutes", "commute_fee", "note",
        ]

    def test_column_definition_includes_subject_period_when_used(self, client, db, setup):
        headers = self._headers(client)
        assert client.post("/api/w/contracts", json=_payload(setup), headers=headers).status_code == 201
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        assert entry["use_period_slots"] is True
        assert "subject_period" in [c["key"] for c in entry["column_definition"]]

    def test_break_hidden_allowed_when_unused(self, client, db, setup):
        # 休憩非表示×コマ設定の併用不可はコマ設定を使用する契約のみ（未使用は自動計算が働かないため許容）
        headers = self._headers(client)
        cases = _default_cases()
        cases[0]["slots"] = self.SLOTS
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, workload_cases=cases, show_break_minutes=False, use_period_slots=False),
            headers=headers,
        )
        assert res.status_code == 201, res.text

    def test_patch_break_hidden_allowed_when_unused(self, client, db, setup):
        headers = self._headers(client)
        cases = _default_cases()
        cases[0]["slots"] = self.SLOTS
        created = client.post(
            "/api/w/contracts", json=_payload(setup, workload_cases=cases, use_period_slots=False), headers=headers,
        )
        assert created.status_code == 201, created.text
        res = client.patch(
            f"/api/w/contracts/{created.json()['id']}", json={"show_break_minutes": False}, headers=headers,
        )
        assert res.status_code == 200, res.text

    def test_patch_enable_slots_rejected_when_break_hidden(self, client, db, setup):
        # 未使用＋休憩非表示の契約を「使用」へ戻すときは、休憩非表示との併用不可を再検証する
        headers = self._headers(client)
        cases = _default_cases()
        cases[0]["slots"] = self.SLOTS
        created = client.post(
            "/api/w/contracts",
            json=_payload(setup, workload_cases=cases, show_break_minutes=False, use_period_slots=False),
            headers=headers,
        )
        assert created.status_code == 201, created.text
        res = client.patch(
            f"/api/w/contracts/{created.json()['id']}", json={"use_period_slots": True}, headers=headers,
        )
        assert res.status_code == 422
        assert "休憩時間" in res.text


class TestSingleTermContracts:
    """担当業務の必須緩和（202607170952）: 前期・後期のうち少なくとも1期でOK。

    設定する期は委託業務名・適用期間が必須。両期を設定した場合のみ期間の重複を検証する。
    """

    def _headers(self, client):
        return _auth(client, "master@x.example.com")

    def test_first_term_only_ok(self, client, db, setup):
        # 前期だけの契約を登録できる（後期は名称・期別設定とも未設定）
        payload = _payload(
            setup,
            tasks=[{"task_name": "数学指導", "task_id": "T1", "contract_id": "C1"}],
            workload_cases=[_default_cases()[0]],
        )
        res = client.post("/api/w/contracts", json=payload, headers=self._headers(client))
        assert res.status_code == 201, res.text
        assert [t["task_name"] for t in res.json()["tasks"]] == ["数学指導"]
        assert len(res.json()["workload_cases"]) == 1
        # 列定義は前期の1列のみ（担当時限あり＝コマ設定は既定の使用）
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        keys = [c["key"] for c in entry["column_definition"] if str(c["key"]).startswith("task_minutes_")]
        assert keys == ["task_minutes_1"]

    def test_second_term_only_ok(self, client, db, setup):
        # 後期だけの契約も登録できる（前期は位置を空にして送る）
        case = {"task_index": 2, "monthly_minutes": 600, "start_date": _PAST, "end_date": _FUTURE}
        payload = _payload(
            setup,
            tasks=[{}, {"task_name": "数学指導（後期）", "task_id": "T2", "contract_id": "C2"}],
            workload_cases=[case],
        )
        res = client.post("/api/w/contracts", json=payload, headers=self._headers(client))
        assert res.status_code == 201, res.text
        body = res.json()
        assert len(body["tasks"]) == 2  # 位置固定（[0]=前期は空・[1]=後期）
        assert (body["tasks"][0]["task_name"] or "") == ""
        assert body["tasks"][1]["task_name"] == "数学指導（後期）"
        entry = client.get("/api/w/contracts/for-tutor", headers=_auth(client, "tutor@x.example.com")).json()[0]
        keys = [c["key"] for c in entry["column_definition"] if str(c["key"]).startswith("task_minutes_")]
        assert keys == ["task_minutes_2"]

    def test_no_terms_rejected(self, client, db, setup):
        # どちらの期も未設定はエラー（報告書の担当業務列が生成できない）
        res = client.post(
            "/api/w/contracts", json=_payload(setup, tasks=[], workload_cases=[]),
            headers=self._headers(client),
        )
        assert res.status_code == 422
        assert "少なくとも1期" in res.text

    def test_configured_term_requires_period(self, client, db, setup):
        # 設定する期（名称あり）は適用期間が必須のまま
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, tasks=[{"task_name": "数学指導"}], workload_cases=[]),
            headers=self._headers(client),
        )
        assert res.status_code == 422
        assert "担当業務（前期）の適用期間" in res.text

    def test_case_only_term_requires_name(self, client, db, setup):
        # 期別設定だけがある期（名称なし）は委託業務名が必須
        res = client.post(
            "/api/w/contracts",
            json=_payload(setup, tasks=[], workload_cases=[_default_cases()[0]]),
            headers=self._headers(client),
        )
        assert res.status_code == 422
        assert "担当業務（前期）の委託業務名は必須です" in res.text

    def test_patch_to_single_term_ok(self, client, db, setup):
        # 両期の既存契約を前期のみへ更新できる（後期を未設定へ戻す）
        headers = self._headers(client)
        created = client.post("/api/w/contracts", json=_payload(setup), headers=headers)
        assert created.status_code == 201, created.text
        res = client.patch(
            f"/api/w/contracts/{created.json()['id']}",
            json={
                "tasks": [{"task_name": "数学指導", "task_id": "11111"}],
                "workload_cases": [_default_cases()[0]],
            },
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert [t["task_name"] for t in res.json()["tasks"]] == ["数学指導"]

    def test_csv_first_term_only_ok(self, client, db, setup):
        # CSVでも後期の列をすべて空欄にした行（前期のみ）を取り込める
        tutor = _add_user(db, "t200@x.example.com", "tutor")
        tutor.user_no = "T200"
        school = _add_user(db, "s200@x.example.com", "school")
        school.user_no = "S200"
        db.commit()
        row = _csv_row("T200", "S200")
        for header in (cis._main_name_h(2), cis._case_start_h(2), cis._case_end_h(2)):
            row[header] = ""
        res = client.post(
            "/api/w/contracts/import",
            files={"file": ("contracts.csv", _csv_bytes([row]), "text/csv")},
            headers=self._headers(client),
        )
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id))
        assert profile.task_name_1 == "数学指導"
        assert profile.task_name_2 is None
        assert len(profile.workload_cases) == 1


class TestContractNo:
    """契約管理番号（202607170952）: 作成順に自動発番・欠番は再利用しない・更新では変わらない。"""

    def _headers(self, client):
        return _auth(client, "master@x.example.com")

    def _second_pair(self, db):
        tutor = _add_user(db, "tutor-b@x.example.com", "tutor")
        school = _add_user(db, "school-b@x.example.com", "school")
        return tutor, school

    def test_sequential_by_creation_order(self, client, db, setup):
        headers = self._headers(client)
        first = client.post("/api/w/contracts", json=_payload(setup), headers=headers)
        assert first.status_code == 201, first.text
        assert first.json()["contract_no"] == 1
        tutor, school = self._second_pair(db)
        second = client.post(
            "/api/w/contracts",
            json=_payload(setup, tutor_id=str(tutor.id), school_id=str(school.id)),
            headers=headers,
        )
        assert second.status_code == 201, second.text
        assert second.json()["contract_no"] == 2

    def test_patch_keeps_number(self, client, db, setup):
        headers = self._headers(client)
        created = client.post("/api/w/contracts", json=_payload(setup), headers=headers)
        cid = created.json()["id"]
        res = client.patch(f"/api/w/contracts/{cid}", json={"our_staff": "変更"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["contract_no"] == created.json()["contract_no"]

    def test_middle_deletion_gap_not_reused(self, client, db, setup):
        # 途中の契約を物理削除しても、残る契約より小さい番号（欠番）へは巻き戻らない（最大値+1）
        headers = self._headers(client)
        first = client.post("/api/w/contracts", json=_payload(setup), headers=headers)
        assert first.json()["contract_no"] == 1
        tutor, school = self._second_pair(db)
        second = client.post(
            "/api/w/contracts",
            json=_payload(setup, tutor_id=str(tutor.id), school_id=str(school.id)),
            headers=headers,
        )
        assert second.json()["contract_no"] == 2
        # 1番（途中の契約）を物理削除 → 次の発番は 3（欠番1は再利用しない）
        assert client.delete(f"/api/w/contracts/{first.json()['id']}?hard=true", headers=headers).status_code == 200
        tutor3 = _add_user(db, "tutor-c@x.example.com", "tutor")
        school3 = _add_user(db, "school-c@x.example.com", "school")
        third = client.post(
            "/api/w/contracts",
            json=_payload(setup, tutor_id=str(tutor3.id), school_id=str(school3.id)),
            headers=headers,
        )
        assert third.status_code == 201, third.text
        assert third.json()["contract_no"] == 3

    def test_csv_import_issues_numbers_and_upsert_keeps(self, client, db, setup):
        headers = self._headers(client)
        tutor = _add_user(db, "t300@x.example.com", "tutor")
        tutor.user_no = "T300"
        school = _add_user(db, "s300@x.example.com", "school")
        school.user_no = "S300"
        db.commit()
        res = client.post(
            "/api/w/contracts/import",
            files={"file": ("contracts.csv", _csv_bytes([_csv_row("T300", "S300")]), "text/csv")},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id))
        assert profile.contract_no == 1
        # 同じ行を再取込（upsert更新）しても番号は変わらない
        res2 = client.post(
            "/api/w/contracts/import",
            files={"file": ("contracts.csv", _csv_bytes([_csv_row("T300", "S300", **{cis.OUR_STAFF: "更新"})]), "text/csv")},
            headers=headers,
        )
        assert res2.status_code == 200, res2.text
        db.refresh(profile)
        assert profile.contract_no == 1
        assert profile.our_staff == "更新"

    def test_export_includes_contract_no_reference(self, client, db, setup):
        headers = self._headers(client)
        assert client.post("/api/w/contracts", json=_payload(setup), headers=headers).status_code == 201
        res = client.get("/api/w/contracts/export", headers=headers)
        assert res.status_code == 200
        text = res.content.decode("utf-8-sig")
        assert cis.CONTRACT_NO_REF in text.splitlines()[0]
        assert "00001" in text

    def test_import_accepts_template_without_contract_no_column(self, client, db, setup):
        # 契約管理番号(参考)列の無い旧テンプレートも取り込める（参考列のため必須にしない）
        tutor = _add_user(db, "t400@x.example.com", "tutor")
        tutor.user_no = "T400"
        school = _add_user(db, "s400@x.example.com", "school")
        school.user_no = "S400"
        db.commit()
        old_headers = [h for h in cis.headers() if h != cis.CONTRACT_NO_REF]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=old_headers)
        writer.writeheader()
        row = _csv_row("T400", "S400")
        writer.writerow({h: row.get(h, "") for h in old_headers})
        res = client.post(
            "/api/w/contracts/import",
            files={"file": ("contracts.csv", buf.getvalue().encode("utf-8-sig"), "text/csv")},
            headers=self._headers(client),
        )
        assert res.status_code == 200, res.text
        profile = db.scalar(__import__("sqlalchemy").select(WorkAssignmentProfile).where(
            WorkAssignmentProfile.tutor_id == tutor.id))
        assert profile.contract_no == 1


class TestContractCopy:
    """契約のコピー新規登録（改修依頼 202607171557）。

    UI はコピー元の全項目＋新しい講師×学校で作成APIを呼ぶ（コピーはクライアント側でプレフィル）。
    バックエンドは既存の作成APIがそのまま担う＝契約番号は自動発番・同一講師×学校は409。
    ここではその「コピー相当のペイロード」が作成APIで正しく通ることを検証する。
    """

    def _second_pair(self, db):
        return (_add_user(db, "tutor-cp@x.example.com", "tutor"),
                _add_user(db, "school-cp@x.example.com", "school"))

    def test_copy_from_existing_creates_new_with_all_fields(self, client, db, setup):
        headers = _auth(client, "master@x.example.com")
        source = client.post(
            "/api/w/contracts",
            json=_payload(setup, work_location="〇〇高校 北校舎", classroom_name="1年A組",
                          scoring_enabled=True, scoring_label="教科会", scoring_unit="回",
                          sub_tasks=[{"task_name": "教材作成", "task_id": "s1", "contract_id": "c1"}]),
            headers=headers,
        ).json()
        tutor2, school2 = self._second_pair(db)
        # UI のコピー相当: コピー元の出力をそのまま流用し、講師・学校のみ差し替えて作成する
        copy_payload = {**source, "tutor_id": str(tutor2.id), "school_id": str(school2.id)}
        res = client.post("/api/w/contracts", json=copy_payload, headers=headers)
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["tutor_id"] == str(tutor2.id)
        assert body["school_id"] == str(school2.id)
        # 全項目が引き継がれる
        assert body["work_location"] == "〇〇高校 北校舎"
        assert body["classroom_name"] == "1年A組"
        assert body["scoring_enabled"] is True
        assert body["scoring_label"] == "教科会"
        assert [t["task_id"] for t in body["tasks"]] == ["11111", "22222"]
        assert body["sub_tasks"][0]["task_name"] == "教材作成"
        # 契約番号は新しく自動発番（コピー元の次番号）
        assert body["contract_no"] == source["contract_no"] + 1
        # コピー元は別レコードとして残る（合計2件）
        assert db.query(WorkAssignmentProfile).count() == 2

    def test_copy_to_same_pair_rejected(self, client, db, setup):
        headers = _auth(client, "master@x.example.com")
        source = client.post("/api/w/contracts", json=_payload(setup), headers=headers).json()
        # 同一講師×学校へのコピーは409（重複契約は作れない）
        copy_payload = {**source, "tutor_id": str(setup["tutor"].id), "school_id": str(setup["school"].id)}
        res = client.post("/api/w/contracts", json=copy_payload, headers=headers)
        assert res.status_code == 409
