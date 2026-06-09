"""
新システム エンドツーエンドシナリオテスト。
典型的なワークフローを1報告書で完走確認する。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession


@pytest.fixture()
def scenario():
    db = TestSession()
    tutor = User(email="tutor@scenario.example.com", role="tutor", roles=["tutor"],
                 display_name="テスト講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="school@scenario.example.com", role="school", roles=["school"],
                  display_name="学校担当", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    sales = User(email="sales@scenario.example.com", role="sales", roles=["sales"],
                 display_name="営業担当", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    office = User(email="office@scenario.example.com", role="office", roles=["office"],
                  display_name="事務担当", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    master = User(email="master@scenario.example.com", role="admin_master", roles=["admin_master"],
                  display_name="管理者", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    chief = User(email="chief@scenario.example.com", role="admin_chief", roles=["admin_chief"],
                 display_name="管理責任者", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school, sales, office, master, chief])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, student_name="シナリオ生徒")
    db.add(assignment)
    db.flush()
    # セッションクローズ前に値を確定させる
    assignment_id = str(assignment.id)
    db.commit()
    db.close()
    return {"assignment_id": assignment_id}


@pytest.fixture()
def client():
    return TestClient(app)


def _tok(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class TestScenario:
    def test_full_normal_approval_flow(self, client, scenario):
        """draft → approved まで通常フロー全完走"""
        tutor_h = _tok(client, "tutor@scenario.example.com")
        assignment_id = scenario["assignment_id"]

        # 作成
        res = client.post("/api/w/reports", json={
            "assignment_id": assignment_id,
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {"lines": [
                {"date": "2026-06-01", "start": "09:00", "end": "11:00",
                 "subject_period": 2, "teach_minutes": 120, "break_minutes": 0,
                 "commute_fee": 500, "note": "数学"}
            ]},
        }, headers=tutor_h)
        assert res.status_code == 201
        report_id = res.json()["id"]
        assert res.json()["status"] == WorkStatus.DRAFT

        # 各ステップで正しいステータスに遷移するか
        steps = [
            ("tutor@scenario.example.com", "submit", None, WorkStatus.AWAITING_SCHOOL),
            ("school@scenario.example.com", "approve", None, WorkStatus.AWAITING_OFFICE),
            ("office@scenario.example.com", "approve", None, WorkStatus.AWAITING_SALES),
            ("sales@scenario.example.com", "approve", None, WorkStatus.AWAITING_FINANCE),
            ("master@scenario.example.com", "approve", None, WorkStatus.APPROVED),
        ]
        for email, action, comment, expected in steps:
            h = _tok(client, email)
            res = client.post(f"/api/w/reports/{report_id}/action",
                              json={"action": action, "comment": comment}, headers=h)
            assert res.status_code == 200, f"step {email}/{action}: {res.text}"
            assert res.json()["status"] == expected

    def test_skip_school_then_full_approval(self, client, scenario):
        """skip_school（管理責任者のみ） → awaiting_office から続けて完全承認"""
        tutor_h = _tok(client, "tutor@scenario.example.com")
        chief_h = _tok(client, "chief@scenario.example.com")

        res = client.post("/api/w/reports", json={
            "assignment_id": scenario["assignment_id"],
            "target_month": "2026-07",
            "form_type": "monthly_dispatch",
            "form_data": {},
        }, headers=tutor_h)
        assert res.status_code == 201, res.text
        report_id = res.json()["id"]

        # 管理責任者がスキップ
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "skip_school"}, headers=chief_h)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE

        # 残りを承認
        for email, action in [
            ("office@scenario.example.com", "approve"),
            ("sales@scenario.example.com", "approve"),
            ("master@scenario.example.com", "approve"),
        ]:
            r = client.post(f"/api/w/reports/{report_id}/action",
                            json={"action": action}, headers=_tok(client, email))
            assert r.status_code == 200

        assert r.json()["status"] == WorkStatus.APPROVED

    def test_return_from_sales_then_resubmit_by_office(self, client, scenario):
        """sales 差戻し → returned_to_office → office が再提出 → awaiting_sales"""
        tutor_h = _tok(client, "tutor@scenario.example.com")
        res = client.post("/api/w/reports", json={
            "assignment_id": scenario["assignment_id"],
            "target_month": "2026-08",
            "form_type": "monthly_dispatch",
            "form_data": {},
        }, headers=tutor_h)
        report_id = res.json()["id"]

        for email, action, comment in [
            ("tutor@scenario.example.com", "submit", None),
            ("school@scenario.example.com", "approve", None),
            ("office@scenario.example.com", "approve", None),
            ("sales@scenario.example.com", "return", "明細に誤りがあります"),
        ]:
            client.post(f"/api/w/reports/{report_id}/action",
                        json={"action": action, "comment": comment},
                        headers=_tok(client, email))

        # returned_to_office 確認
        res = client.get(f"/api/w/reports/{report_id}",
                         headers=_tok(client, "office@scenario.example.com"))
        assert res.json()["status"] == WorkStatus.RETURNED_TO_OFFICE

        # office が再提出
        res = client.post(f"/api/w/reports/{report_id}/action",
                          json={"action": "submit"},
                          headers=_tok(client, "office@scenario.example.com"))
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_SALES

    def test_duplicate_month_blocked_at_db(self, client, scenario):
        """同月二重作成は 409 で弾かれる"""
        tutor_h = _tok(client, "tutor@scenario.example.com")
        payload = {
            "assignment_id": scenario["assignment_id"],
            "target_month": "2026-09",
            "form_type": "monthly_dispatch",
            "form_data": {},
        }
        res1 = client.post("/api/w/reports", json=payload, headers=tutor_h)
        assert res1.status_code == 201
        res2 = client.post("/api/w/reports", json=payload, headers=tutor_h)
        assert res2.status_code == 409

    def test_events_are_recorded(self, client, scenario):
        """ワークフローイベントが記録されていること"""
        tutor_h = _tok(client, "tutor@scenario.example.com")
        res = client.post("/api/w/reports", json={
            "assignment_id": scenario["assignment_id"],
            "target_month": "2026-10",
            "form_type": "monthly_dispatch",
            "form_data": {},
        }, headers=tutor_h)
        report_id = res.json()["id"]

        client.post(f"/api/w/reports/{report_id}/action",
                    json={"action": "submit"}, headers=tutor_h)

        res = client.get(f"/api/w/reports/{report_id}/events", headers=tutor_h)
        assert res.status_code == 200
        events = res.json()
        assert len(events) == 1
        assert events[0]["action"] == "submit"
        assert events[0]["from_status"] == WorkStatus.DRAFT
        assert events[0]["to_status"] == WorkStatus.AWAITING_SCHOOL
