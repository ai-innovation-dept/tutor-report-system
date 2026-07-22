"""改修 2026072120937 ①: 講師の下書き削除が「対象月ちがい」で失敗するバグの回帰テスト。

背景（不具合）:
  承認管理（/tutor/approval）は報告書を (assignment_id, target_month) 単位のグループで表示するが、
  削除・提出・再依頼・差戻し要求・詳細表示の各操作が group を assignment_id だけで解決していた
  （`groups.find(item => item.assignment_id === assignmentId)`）。このため、同じ学校（assignment）に
  複数月の下書き／差戻しがあると、ボタンを押した月ではなく配列で先頭に来た別の月のグループを掴み、
  「2月分の削除を押しても消えない／3月分が何度目かでやっと消える」という不審挙動になっていた。

修正:
  各操作を (assignment_id, target_month) の両方で解決する findGroup(assignmentId, targetMonth) に統一し、
  各ボタンから group.target_month を渡すようにした（フロントのみ・DB/API変更なし）。

実メール送信ゼロ: conftest が MAIL_BACKEND=console を強制するため、本テストで実SMTP送信は起きない。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
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


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture()
def users(db):
    tutor = User(email="d-tutor@example.com", role="tutor", roles=["tutor"],
                 display_name="悟史講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="d-school@example.com", role="school", roles=["school"],
                  display_name="テスト学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="削除テスト", system_type="new")
    db.add(assignment)
    db.flush()
    db.add(WorkAssignmentProfile(assignment_id=assignment.id, tutor_id=tutor.id, school_id=school.id, form_type="monthly_dispatch"))
    db.commit()
    return {"tutor": tutor, "school": school, "assignment": assignment}


def _create_draft(client, users, month):
    res = client.post("/api/w/reports", json={
        "assignment_id": str(users["assignment"].id),
        "target_month": month,
        "form_type": "monthly_dispatch",
        "form_data": {"lines": [{"date": f"{month}-05", "start": "09:00", "end": "10:00", "teach_minutes": 60}]},
    }, headers=_auth(client, "d-tutor@example.com"))
    assert res.status_code == 201, res.text
    return res.json()["id"]


class TestDraftDeletePerMonth:
    """同一 assignment に複数月の下書きがあっても、指定した月だけを正しく削除できる。"""

    def test_deleting_feb_leaves_march_intact(self, client, users):
        headers = _auth(client, "d-tutor@example.com")
        feb = _create_draft(client, users, "2026-02")
        mar = _create_draft(client, users, "2026-03")

        # 2月分だけを削除
        res = client.delete(f"/api/w/reports/{feb}", headers=headers)
        assert res.status_code == 204, res.text

        remaining = {r["target_month"]: r for r in client.get("/api/w/reports", headers=headers).json()}
        assert "2026-02" not in remaining              # 2月は消えた
        assert "2026-03" in remaining                  # 3月は残っている
        assert remaining["2026-03"]["id"] == mar
        assert remaining["2026-03"]["status"] == WorkStatus.DRAFT

    def test_deleting_march_then_feb_both_removed(self, client, users):
        headers = _auth(client, "d-tutor@example.com")
        feb = _create_draft(client, users, "2026-02")
        mar = _create_draft(client, users, "2026-03")
        assert client.delete(f"/api/w/reports/{mar}", headers=headers).status_code == 204
        assert client.delete(f"/api/w/reports/{feb}", headers=headers).status_code == 204
        assert client.get("/api/w/reports", headers=headers).json() == []


class TestApprovalPageWiringIsMonthAware:
    """/tutor/approval の各操作が (assignment_id, target_month) 両方でグループ解決していること。"""

    HANDLERS = ["deleteDraftReports", "submitToParentGroup", "resendReturnedReports",
                "requestReturnGroup", "toggleDetails"]

    def test_group_actions_pass_target_month(self, client, users):
        _auth(client, "d-tutor@example.com")
        html = client.get("/tutor/approval").text

        # 月違いを起こしていた旧パターンは残っていない
        assert "groups.find(item => item.assignment_id === assignmentId)" not in html
        # 月まで見て解決するヘルパーへ統一されている
        assert "function findGroup(assignmentId, targetMonth)" in html
        assert "item.assignment_id === assignmentId && item.target_month === targetMonth" in html
        # 各ボタンは target_month を渡している
        for name in self.HANDLERS:
            assert f"{name}('${{group.assignment_id}}', '${{group.target_month}}')" in html, name
