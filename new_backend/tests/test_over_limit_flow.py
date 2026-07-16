"""月分超過時の承認フロー切替テスト。

担当業務の対象月の分数合計が、契約に登録した月分固定（task_index で紐づくケース）を
超えて提出された報告は、学校確認の前に事務の事前確認を挟む超過フローになる:
  講師 → 事務(事前確認) → 学校 → 事務 → 営業
学校承認スキップ校は事務確認1回（講師 → 事務 → 営業）のまま。
"""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import User
from app.models.work import WorkMailOutbox, WorkNotification, WorkReport
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
    return {
        "master": _add_user(db, "master@ol.example.com", "admin_master"),
        "tutor": _add_user(db, "tutor@ol.example.com", "tutor"),
        "school": _add_user(db, "school@ol.example.com", "school"),
        "office": _add_user(db, "office@ol.example.com", "office"),
        "sales": _add_user(db, "sales@ol.example.com", "sales"),
    }


# 既定の期別設定: 前期=2026-04〜08（対象月2026-06を含む）／後期=2026-09〜2027-03
DEFAULT_CASES = [
    {"task_index": 1, "monthly_minutes": 1200, "weekly_lessons": 6,
     "start_date": "2026-04-01", "end_date": "2026-08-31"},
    {"task_index": 2, "start_date": "2026-09-01", "end_date": "2027-03-31"},
]


def _create_contract(client, setup, workload_cases=None):
    payload = {
        "tutor_id": str(setup["tutor"].id),
        "school_id": str(setup["school"].id),
        "contract_start": "2026-04-01",
        "contract_end": "2027-03-31",
        "tasks": [
            {"task_name": "数学科指導", "task_id": "T1", "contract_id": "C1"},
            {"task_name": "数学科指導（後期）", "task_id": "T2", "contract_id": "C2"},
        ],
        "workload_cases": workload_cases if workload_cases is not None else DEFAULT_CASES,
    }
    res = client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@ol.example.com"))
    assert res.status_code == 201, res.text
    return res.json()


def _create_report(client, contract, task_minutes_total):
    # 担当業務①（task_minutes_1）に合計 task_minutes_total 分の明細を入れる
    lines = [
        {"date": "2026-06-01", "start": "09:00", "end": "14:00", "task_minutes_1": task_minutes_total - 30},
        {"date": "2026-06-02", "start": "09:00", "end": "10:00", "task_minutes_1": 30},
    ]
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": contract["assignment_id"],
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {"lines": lines, "meta": {}},
        },
        headers=_auth(client, "tutor@ol.example.com"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _action(client, email, report_id, action, role, comment=None):
    return client.post(
        f"/api/w/reports/{report_id}/action",
        json={"action": action, "actor_role": role, "comment": comment},
        headers=_auth(client, email),
    )


class TestOverLimitFlow:
    def test_over_limit_submit_goes_to_office_precheck(self, client, db, setup):
        """月1200分固定に対して合計1230分 → 提出で事務の事前確認へ。通知も事務宛。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, 1230)
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK
        notifications = list(db.scalars(select(WorkNotification)))
        assert any(
            n.user_id == setup["office"].id and n.type == "approval_request" for n in notifications
        )
        assert not any(n.user_id == setup["school"].id for n in notifications)

    def test_at_limit_stays_normal_flow(self, client, db, setup):
        """合計がちょうど月分固定（1200分）なら超過ではなく通常フロー（学校確認待ち）。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, 1200)
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_over_limit_full_flow_to_approved(self, client, db, setup):
        """超過フロー全体: 講師→事務(事前確認)→学校→事務→営業→完了。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, 1230)
        assert _action(client, "tutor@ol.example.com", report_id, "submit", "tutor").json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK
        # 事務の事前確認 → 学校確認待ちへ合流
        res = _action(client, "office@ol.example.com", report_id, "approve", "office")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL
        school_mails = list(
            db.scalars(
                select(WorkMailOutbox).where(
                    WorkMailOutbox.to_email == "school@ol.example.com",
                    WorkMailOutbox.subject == "【業務連絡表】承認依頼が届きました",
                    WorkMailOutbox.status == "pending",
                )
            )
        )
        assert len(school_mails) == 1
        assert _action(client, "school@ol.example.com", report_id, "approve", "school").json()["status"] == WorkStatus.AWAITING_OFFICE
        assert _action(client, "office@ol.example.com", report_id, "approve", "office").json()["status"] == WorkStatus.AWAITING_SALES
        assert _action(client, "sales@ol.example.com", report_id, "approve", "sales").json()["status"] == WorkStatus.APPROVED

    def test_over_limit_with_school_skip_goes_to_office_once(self, client, db, setup):
        """学校承認スキップ校は超過でも事務確認1回（講師→事務→営業）。"""
        setup["school"].skip_parent_approval = True
        db.commit()
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, 1230)
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE
        assert _action(client, "office@ol.example.com", report_id, "approve", "office").json()["status"] == WorkStatus.AWAITING_SALES

    def test_precheck_return_requires_comment_and_resubmit_rechecks(self, client, db, setup):
        """事前確認からの差戻しはコメント必須で講師へ。再提出時も超過なら再び事前確認へ。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, 1230)
        _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        # コメントなしの差戻しは422
        assert _action(client, "office@ol.example.com", report_id, "return", "office").status_code == 422
        res = _action(client, "office@ol.example.com", report_id, "return", "office", comment="月分超過の内容を確認してください")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.RETURNED_TO_TUTOR
        # 修正せず再提出 → 依然超過のため再び事前確認へ
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK

    def test_no_limit_keeps_normal_flow(self, client, db, setup):
        """月時間（分）未設定の期は分数（10分単位）に関係なく通常フロー。

        ※99990は10分単位の値。1〜9分単位の手入力は別トリガー（test_minute_input_flow.py）で
        事前確認フローになるため、このテストでは10分単位の大きな値を使う。
        """
        cases = [
            {"task_index": 1, "start_date": "2026-04-01", "end_date": "2026-08-31"},
            {"task_index": 2, "start_date": "2026-09-01", "end_date": "2027-03-31"},
        ]
        contract = _create_contract(client, setup, workload_cases=cases)
        report_id = _create_report(client, contract, 99990)
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_case_outside_target_month_not_applied(self, client, db, setup):
        """対象月（2026-06）が適用期間外の期の上限は判定に使われない。"""
        cases = [
            {"task_index": 1, "monthly_minutes": 1200, "weekly_lessons": 6,
             "start_date": "2026-09-01", "end_date": "2026-12-31"},
            {"task_index": 2, "start_date": "2027-01-01", "end_date": "2027-03-31"},
        ]
        contract = _create_contract(client, setup, workload_cases=cases)
        report_id = _create_report(client, contract, 5000)  # 2026-06 はどの期にも該当しない
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_second_term_limit_applies_in_its_period(self, client, db, setup):
        """後期の月（2026-10）の報告は後期の上限（task_minutes_2）で判定される。"""
        cases = [
            {"task_index": 1, "monthly_minutes": 1200, "start_date": "2026-04-01", "end_date": "2026-08-31"},
            {"task_index": 2, "monthly_minutes": 600, "start_date": "2026-09-01", "end_date": "2027-03-31"},
        ]
        contract = _create_contract(client, setup, workload_cases=cases)
        lines = [
            {"date": "2026-10-01", "start": "09:00", "end": "14:00", "task_minutes_2": 600},
            {"date": "2026-10-02", "start": "09:00", "end": "10:00", "task_minutes_2": 30},
        ]
        res = client.post(
            "/api/w/reports",
            json={
                "assignment_id": contract["assignment_id"],
                "target_month": "2026-10",
                "form_type": "monthly_dispatch",
                "form_data": {"lines": lines, "meta": {}},
            },
            headers=_auth(client, "tutor@ol.example.com"),
        )
        assert res.status_code == 201, res.text
        submitted = _action(client, "tutor@ol.example.com", res.json()["id"], "submit", "tutor")
        assert submitted.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK


class TestWeeklyLessonsFlow:
    """担当時限の週コマ数が契約の週コマ（期別）を超えた提出の事前確認フロー切替。

    週は月曜〜日曜の暦週。2026-06-01 は月曜のため、6/1〜6/7 が1週。
    既定契約（DEFAULT_CASES）の前期は週6コマ。
    """

    def _report_with_periods(self, client, contract, day_periods, month="2026-06"):
        # day_periods: {"2026-06-01": "1・2・3", ...}（担当時限の選択コマ）
        lines = [
            {"date": day, "subject_period": periods, "task_minutes_1": 100}
            for day, periods in day_periods.items()
        ]
        res = client.post(
            "/api/w/reports",
            json={
                "assignment_id": contract["assignment_id"],
                "target_month": month,
                "form_type": "monthly_dispatch",
                "form_data": {"lines": lines, "meta": {}},
            },
            headers=_auth(client, "tutor@ol.example.com"),
        )
        assert res.status_code == 201, res.text
        return res.json()["id"]

    def test_weekly_over_goes_to_office_precheck(self, client, db, setup):
        """週6コマ契約で同一週（6/1月〜6/7日）に7コマ → 事前確認へ。"""
        contract = _create_contract(client, setup)
        report_id = self._report_with_periods(client, contract, {
            "2026-06-01": "1・2・3",
            "2026-06-03": "1・2",
            "2026-06-05": "1・2",
        })
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK

    def test_weekly_at_limit_stays_normal(self, client, db, setup):
        """週あたりちょうど6コマなら通常フロー（超過ではない）。"""
        contract = _create_contract(client, setup)
        report_id = self._report_with_periods(client, contract, {
            "2026-06-01": "1・2・3",
            "2026-06-03": "1・2・3",
            "2026-06-08": "1・2・3",  # 翌週（6/8月〜）は別カウント
            "2026-06-10": "1・2・3",
        })
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_weekly_count_not_split_across_weeks(self, client, db, setup):
        """日曜と翌月曜は別の暦週として数える（6/7日曜4コマ＋6/8月曜4コマ=各週4コマ→通常フロー）。"""
        contract = _create_contract(client, setup)
        report_id = self._report_with_periods(client, contract, {
            "2026-06-07": "1・2・3・4",
            "2026-06-08": "1・2・3・4",
        })
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_weekly_ignores_leave_lines(self, client, db, setup):
        """有給休暇・欠勤の行の担当時限は週コマ数に数えない。"""
        contract = _create_contract(client, setup)
        report_id = self._report_with_periods(client, contract, {
            "2026-06-01": "1・2・3",
            "2026-06-03": "1・2・3",
        })
        # 同週に有給行（担当時限が残っていても数えない）を追加しても超過にならない
        report = db.get(WorkReport, uuid.UUID(report_id))
        lines = list(report.form_data["lines"])
        lines.append({"date": "2026-06-05", "subject_period": "1・2・3", "kind": "paid_leave"})
        report.form_data = {**report.form_data, "lines": lines}
        db.commit()
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_weekly_no_limit_no_precheck(self, client, db, setup):
        """週コマ未設定の期はコマ数に関係なく通常フロー。"""
        cases = [
            {"task_index": 1, "start_date": "2026-04-01", "end_date": "2026-08-31"},
            {"task_index": 2, "start_date": "2026-09-01", "end_date": "2027-03-31"},
        ]
        contract = _create_contract(client, setup, workload_cases=cases)
        report_id = self._report_with_periods(client, contract, {
            "2026-06-01": "1・2・3・4・5",
            "2026-06-02": "1・2・3・4・5",
        })
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_weekly_over_with_school_skip_goes_to_office_once(self, client, db, setup):
        """学校承認スキップ校は週コマ超過でも事務確認1回（講師→事務→営業）。"""
        setup["school"].skip_parent_approval = True
        db.commit()
        contract = _create_contract(client, setup)
        report_id = self._report_with_periods(client, contract, {
            "2026-06-01": "1・2・3・4",
            "2026-06-03": "1・2・3",
        })
        res = _action(client, "tutor@ol.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE
        assert _action(client, "office@ol.example.com", report_id, "approve", "office").json()["status"] == WorkStatus.AWAITING_SALES
