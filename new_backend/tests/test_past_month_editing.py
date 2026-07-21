"""
過去月の業務連絡表を編集・提出・承認できることの検証（改修依頼 202607211716・案B / EMPS）。

背景:
  ルーズな講師が「月をまたいでから」業務連絡表を入力するケースを救済するため、
  「当月のみ操作可」だったフロントの月ゲートを撤廃し、講師の入力可否・承認側の操作可否を
  すべて「報告書のステータス」で判定する（＝過去月も当月と同じ扱い）。
  バックエンドは元々ワークフロー遷移に月チェックが無いため、本改修はフロント（テンプレート）中心。

このテストで確認すること:
  1. 過去月でも 作成 → 編集(PATCH) → 提出 → 学校承認 → 事務承認 → 営業承認(完了) まで完走できる
     （＝将来バックエンドに月ゲートが混入した場合の回帰検知）。
  2. 提出後（awaiting_school 以降）の過去月報告書は編集不可（月ではなくステータスで弾く＝当月と同じ）。
  3. 承認フロー各画面のフロント月ゲート
     （「過去月は参照のみです」「過去月のため承認・差戻しはできません」等）が撤廃されていること。
  4. テスト環境では実メールを一切送らない（MAIL_BACKEND=console 固定＝承認時もSMTP実送信なし）。

実メール送信ゼロ: conftest が MAIL_BACKEND=console を強制するため、承認操作を含め実SMTP送信は起きない。
"""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession


def _past_month(months_back: int = 2) -> str:
    """現在から months_back ヶ月前の 'YYYY-MM'（＝当月より過去の対象月）。"""
    y, m = date.today().year, date.today().month
    m -= months_back
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def users(db):
    tutor = User(email="pm-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="pm-school@example.com", role="school", roles=["school"], display_name="学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    office = User(email="pm-office@example.com", role="office", roles=["office"], display_name="事務", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    sales = User(email="pm-sales@example.com", role="sales", roles=["sales"], display_name="営業", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school, office, sales])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="過去月テスト生徒", system_type="new")
    db.add(assignment)
    db.flush()
    db.add(WorkAssignmentProfile(assignment_id=assignment.id, tutor_id=tutor.id, school_id=school.id, form_type="monthly_dispatch"))
    db.commit()
    return {"tutor": tutor, "school": school, "office": office, "sales": sales, "assignment": assignment}


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_past_month_report(client, users, month):
    res = client.post("/api/w/reports", json={
        "assignment_id": str(users["assignment"].id),
        "target_month": month,
        "form_type": "monthly_dispatch",
        "form_data": {"lines": [{"date": f"{month}-05", "start": "09:00", "end": "10:00", "teach_minutes": 60}]},
    }, headers=_auth(client, "pm-tutor@example.com"))
    assert res.status_code == 201, res.text
    return res.json()["id"]


class TestPastMonthFlow:
    def test_tutor_can_create_and_edit_past_month_draft(self, client, users):
        """過去月の下書きを作成し、そのまま編集（PATCH）できる（＝月ゲートで弾かれない）。"""
        month = _past_month()
        headers = _auth(client, "pm-tutor@example.com")
        report_id = _create_past_month_report(client, users, month)
        res = client.patch(f"/api/w/reports/{report_id}", json={
            "form_data": {"lines": [
                {"date": f"{month}-05", "start": "09:00", "end": "11:00", "teach_minutes": 120},
                {"date": f"{month}-12", "start": "09:00", "end": "10:00", "teach_minutes": 60},
            ]},
        }, headers=headers)
        assert res.status_code == 200, res.text

    def test_past_month_report_flows_through_full_approval(self, client, users):
        """過去月でも 提出 → 学校 → 事務 → 営業 の承認フローを完走できる（当月と同一遷移）。"""
        month = _past_month()
        report_id = _create_past_month_report(client, users, month)
        steps = [
            ("pm-tutor@example.com", "submit", WorkStatus.AWAITING_SCHOOL),
            ("pm-school@example.com", "approve", WorkStatus.AWAITING_OFFICE),
            ("pm-office@example.com", "approve", WorkStatus.AWAITING_SALES),
            ("pm-sales@example.com", "approve", WorkStatus.APPROVED),
        ]
        for email, action, expected in steps:
            res = client.post(f"/api/w/reports/{report_id}/action",
                              json={"action": action}, headers=_auth(client, email))
            assert res.status_code == 200, f"{email}/{action}: {res.text}"
            assert res.json()["status"] == expected, f"{email}/{action} -> {res.json()['status']}"

    def test_submitted_past_month_report_is_not_editable(self, client, users):
        """提出後（awaiting_school）の過去月報告書は編集不可（月ではなくステータスで弾く＝当月と同じ挙動）。"""
        month = _past_month()
        headers = _auth(client, "pm-tutor@example.com")
        report_id = _create_past_month_report(client, users, month)
        assert client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers).status_code == 200
        res = client.patch(f"/api/w/reports/{report_id}", json={"form_data": {"lines": []}}, headers=headers)
        assert res.status_code == 409, res.text


class TestPastMonthFrontendGates:
    """フロントの当月ゲートが撤廃されていること（テンプレートに旧ゲート文言・条件が残っていない）。"""

    def test_tutor_reports_has_no_past_month_readonly_gate(self, client, users):
        _auth(client, "pm-tutor@example.com")
        html = client.get("/tutor/reports").text
        assert 'id="monthFilter"' in html  # 業務連絡表（報告書一覧）ページが取得できている前提
        # 旧: 過去月はフォーム全体を読取専用にしていた
        assert "過去月は参照のみです" not in html
        # 承認依頼ボタンの当月限定条件が撤廃されている
        assert "report.target_month === currentMonth" not in html

    def test_tutor_approval_has_no_past_month_request_exclusion(self, client, users):
        _auth(client, "pm-tutor@example.com")
        html = client.get("/tutor/approval").text
        # 旧: 過去月の awaiting_school は差戻し要求から除外していた
        assert "pastMonth" not in html

    def test_school_approval_has_no_past_month_gate(self, client, users):
        _auth(client, "pm-school@example.com")
        html = client.get("/school/approval").text
        assert "過去月のため承認・差戻しはできません" not in html

    def test_report_view_school_action_has_no_past_month_gate(self, client, users):
        report_id = _create_past_month_report(client, users, _past_month())
        _auth(client, "pm-school@example.com")
        html = client.get(f"/reports/{report_id}/view").text
        assert "過去月のため承認・差戻しはできません" not in html
        # 差戻し要求への学校対応可否から当月限定が外れている
        assert "report.target_month !== CURRENT_MONTH" not in html


class TestNoRealMail:
    def test_environment_sends_no_real_mail(self):
        """テスト環境は MAIL_BACKEND!=smtp 固定＝承認等でSMTP実送信は起きない（本番メール誤送信の防止）。

        mailer._deliver / drain_outbox はいずれも MAIL_BACKEND=='smtp' のときだけ実送信するため、
        この条件が成り立てば承認操作を含め実メールは一切送られない。
        """
        from app.core.config import settings
        assert (settings.MAIL_BACKEND or "console").lower() != "smtp"
