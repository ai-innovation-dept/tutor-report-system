"""担当業務・副担当業務への1〜9分単位の手入力時の承認フロー切替テスト。

担当業務（task_minutes_N / teach_minutes）・副担当業務（sub_minutes_N）に
10分単位でない値（1の位が1〜9）がある報告は、学校確認の前に事務の事前確認を挟む:
  講師 → 事務(事前確認) → 学校 → 事務 → 営業
休憩時間・採点（分）・交通費・有給/欠勤行は判定対象外。
学校承認スキップ校は事務確認1回（講師 → 事務 → 営業）のまま。
発動理由は提出イベントのコメントに自動記録される。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import User
from app.models.work import WorkMailOutbox, WorkNotification, WorkReportEvent
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
        "master": _add_user(db, "master@mi.example.com", "admin_master"),
        "tutor": _add_user(db, "tutor@mi.example.com", "tutor"),
        "school": _add_user(db, "school@mi.example.com", "school"),
        "office": _add_user(db, "office@mi.example.com", "office"),
        "sales": _add_user(db, "sales@mi.example.com", "sales"),
    }


def _create_contract(client, setup):
    payload = {
        "tutor_id": str(setup["tutor"].id),
        "school_id": str(setup["school"].id),
        "contract_start": "2026-04-01",
        "contract_end": "2027-03-31",
        "tasks": [
            {"task_name": "数学科指導", "task_id": "T1", "contract_id": "C1"},
            {"task_name": "数学科指導（後期）", "task_id": "T2", "contract_id": "C2"},
        ],
        "sub_tasks": [{"task_name": "採点補助"}],
        # 月時間・週コマは未設定（このテストの事前確認トリガーは1〜9分手入力のみ）
        "workload_cases": [
            {"task_index": 1, "start_date": "2026-04-01", "end_date": "2026-08-31"},
            {"task_index": 2, "start_date": "2026-09-01", "end_date": "2027-03-31"},
        ],
    }
    res = client.post("/api/w/contracts", json=payload, headers=_auth(client, "master@mi.example.com"))
    assert res.status_code == 201, res.text
    return res.json()


def _create_report(client, contract, lines):
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": contract["assignment_id"],
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {"lines": lines, "meta": {}},
        },
        headers=_auth(client, "tutor@mi.example.com"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _action(client, email, report_id, action, role, comment=None):
    return client.post(
        f"/api/w/reports/{report_id}/action",
        json={"action": action, "actor_role": role, "comment": comment},
        headers=_auth(client, email),
    )


def _patch_lines(client, report_id, lines):
    res = client.patch(
        f"/api/w/reports/{report_id}",
        json={"form_data": {"lines": lines, "meta": {}}},
        headers=_auth(client, "tutor@mi.example.com"),
    )
    assert res.status_code == 200, res.text
    return res.json()


class TestMinuteInputFlow:
    def test_minute_input_in_main_task_goes_to_office_precheck(self, client, db, setup):
        """担当業務に1の位1〜9の値（151分）→ 提出で事務の事前確認へ。通知も事務宛。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "start": "08:40", "end": "11:31", "task_minutes_1": 151, "break_minutes": 20},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK
        notifications = list(db.scalars(select(WorkNotification)))
        assert any(n.user_id == setup["office"].id and n.type == "approval_request" for n in notifications)
        assert not any(n.user_id == setup["school"].id for n in notifications)

    def test_minute_input_in_sub_task_goes_to_office_precheck(self, client, db, setup):
        """副担当業務に1〜9分の値（5分）でも事前確認へ。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 150, "sub_minutes_1": 5},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK

    def test_round_minutes_stay_normal_flow(self, client, db, setup):
        """担当・副担当がすべて10分単位（150/30）なら通常フロー（学校確認待ち）。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 150, "sub_minutes_1": 30, "break_minutes": 20},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_break_or_fee_minutes_do_not_trigger(self, client, db, setup):
        """休憩時間・交通費の1の位が1〜9でも判定対象外（担当・副担当のみ対象）。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 150, "break_minutes": 15, "commute_fee": 1234},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_leave_row_values_do_not_trigger(self, client, db, setup):
        """有給・欠勤（kind）の行に残った値は判定対象外。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 150},
            {"date": "2026-06-02", "kind": "paid_leave", "task_minutes_1": 151},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_no_main_duty_row_sub_minutes_trigger(self, client, db, setup):
        """自己都合・学校行事（kind）の行は担当業務0固定だが、副業務への1〜9分手入力は判定対象。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 150},
            {"date": "2026-06-02", "kind": "school_event", "task_minutes_1": 0, "sub_minutes_1": 37},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK

    def test_no_main_duty_row_round_minutes_stay_normal_flow(self, client, db, setup):
        """自己都合（kind）の行が担当業務0・副業務10分単位なら通常フロー（学校確認待ち）。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 150},
            {"date": "2026-06-02", "kind": "personal_reason", "task_minutes_1": 0, "sub_minutes_1": 30},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

    def test_default_form_teach_minutes_triggers(self, client, db, setup):
        """デフォルト列（teach_minutes＝担当業務）でも判定される。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "teach_minutes": 61},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK

    def test_precheck_reason_recorded_in_submit_event(self, client, db, setup):
        """発動理由（1〜9分手入力）が提出イベントのコメントに自動記録される。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 151},
        ])
        _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        events = list(db.scalars(select(WorkReportEvent).where(WorkReportEvent.action == "submit")))
        assert events, "submit イベントが記録されていること"
        assert any(e.comment and "事前確認" in e.comment and "1〜9分" in e.comment for e in events)

    def test_full_flow_and_precheck_approved_at(self, client, db, setup):
        """事前確認フロー全体: 講師→事務(事前確認)→学校→事務→営業。precheck_approved_at が返る。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 151},
        ])
        assert _action(client, "tutor@mi.example.com", report_id, "submit", "tutor").json()["status"] == WorkStatus.AWAITING_OFFICE_PRECHECK
        res = _action(client, "office@mi.example.com", report_id, "approve", "office")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == WorkStatus.AWAITING_SCHOOL
        assert body["precheck_approved_at"], "事前確認の承認日時が ReportOut に含まれること"
        school_mails = list(
            db.scalars(
                select(WorkMailOutbox).where(
                    WorkMailOutbox.to_email == "school@mi.example.com",
                    WorkMailOutbox.subject == "【業務連絡表】承認依頼が届きました",
                    WorkMailOutbox.status == "pending",
                )
            )
        )
        assert len(school_mails) == 1
        assert _action(client, "school@mi.example.com", report_id, "approve", "school").json()["status"] == WorkStatus.AWAITING_OFFICE
        assert _action(client, "office@mi.example.com", report_id, "approve", "office").json()["status"] == WorkStatus.AWAITING_SALES
        assert _action(client, "sales@mi.example.com", report_id, "approve", "sales").json()["status"] == WorkStatus.APPROVED

    def test_school_skip_keeps_office_once(self, client, db, setup):
        """学校承認スキップ校は1〜9分手入力でも事務確認1回（講師→事務→営業）。"""
        setup["school"].skip_parent_approval = True
        db.commit()
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 151},
        ])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE
        assert _action(client, "office@mi.example.com", report_id, "approve", "office").json()["status"] == WorkStatus.AWAITING_SALES

    def test_resubmit_after_fix_returns_to_normal_flow(self, client, db, setup):
        """差戻し後に10分単位へ修正して再提出すると通常フロー（学校確認待ち）へ。"""
        contract = _create_contract(client, setup)
        report_id = _create_report(client, contract, [
            {"date": "2026-06-01", "task_minutes_1": 151},
        ])
        _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        res = _action(client, "office@mi.example.com", report_id, "return", "office", comment="分数を確認してください")
        assert res.json()["status"] == WorkStatus.RETURNED_TO_TUTOR
        _patch_lines(client, report_id, [{"date": "2026-06-01", "task_minutes_1": 150}])
        res = _action(client, "tutor@mi.example.com", report_id, "submit", "tutor")
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL
