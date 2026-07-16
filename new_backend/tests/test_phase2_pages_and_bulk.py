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
def users(db):
    tutor = User(email="p2-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="p2-school@example.com", role="school", roles=["school"], display_name="学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    sales = User(email="p2-sales@example.com", role="sales", roles=["sales"], display_name="営業", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    office = User(email="p2-office@example.com", role="office", roles=["office"], display_name="事務", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    master = User(email="p2-master@example.com", role="admin_master", roles=["admin_master"], display_name="管理者", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school, sales, office, master])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, student_name="第2弾生徒", system_type="new")
    db.add(assignment)
    db.flush()
    db.add(WorkAssignmentProfile(assignment_id=assignment.id, tutor_id=tutor.id, school_id=school.id, form_type="monthly_dispatch"))
    db.commit()
    return {"tutor": tutor, "school": school, "sales": sales, "office": office, "master": master, "assignment": assignment}


@pytest.fixture()
def client():
    return TestClient(app)


def _login(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create_report(client, users, month="2026-06"):
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": str(users["assignment"].id),
            "target_month": month,
            "form_type": "monthly_dispatch",
            "form_data": {"lines": [{"date": f"{month}-01", "teach_minutes": 90, "break_minutes": 10, "commute_fee": 700}]},
        },
        headers=_login(client, "p2-tutor@example.com"),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


class TestPhase2Pages:
    @pytest.mark.parametrize(
        ("path", "email"),
        [
            ("/tutor/approval", "p2-tutor@example.com"),
            ("/sales/queue", "p2-sales@example.com"),
            ("/office/queue", "p2-office@example.com"),
            ("/finance/queue", "p2-master@example.com"),
        ],
    )
    def test_pages_redirect_when_anonymous_and_200_when_authenticated(self, client, users, path, email):
        anon = TestClient(app)
        res = anon.get(path, follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"

        _login(client, email)
        res = client.get(path)
        assert res.status_code == 200

    def test_admin_report_detail_anonymous_redirects(self, client, users):
        report_id = _create_report(client, users)
        anon = TestClient(app)
        res = anon.get(f"/admin/reports/{report_id}", follow_redirects=False)
        assert res.status_code == 302
        assert res.headers["location"] == "/login"

    def test_tutor_reports_page_has_mobile_line_ui(self, client, users):
        """講師の報告書一覧にスマホ入力UI（明細リスト＋詳細シート）が含まれる。
        md未満では明細テーブルを隠してリスト表示し、行タップでシートを開いて入力する。"""
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/reports")
        assert res.status_code == 200
        assert 'id="mobileLineList"' in res.text
        assert 'id="lineSheetOverlay"' in res.text
        # 明細テーブルはPC（md以上）のみ表示
        assert 'class="hidden overflow-x-auto rounded-lg border border-slate-200 md:block"' in res.text

    def test_shared_work_report_calc_core_included(self, client, users):
        """明細の入力・自動計算の共通コア(work_report_calc.js)が講師の報告書一覧と
        事務ダッシュボード（報告書修正モーダル）の両方で読み込まれ、静的配信される。
        事務修正は講師フォームと同一の入力仕様（種別・担当時限・自動計算・集計）を共有する。"""
        _login(client, "p2-tutor@example.com")
        res = client.get("/tutor/reports")
        assert res.status_code == 200
        assert '/static/js/work_report_calc.js' in res.text
        # コマ設定契約の「副担当の位置」（コマ後/コマ間）列（202607161853）: 講師フォーム（行＋シート）に組み込み済み
        assert 'data-field="secondary_placement"' in res.text
        assert 'data-sheet-field="secondary_placement"' in res.text
        _login(client, "p2-office@example.com")
        res = client.get("/office/queue")
        assert res.status_code == 200
        assert '/static/js/work_report_calc.js' in res.text
        assert 'id="officeEditSummary"' in res.text  # 修正モーダルの集計欄
        # 事務修正モーダルも「副担当の位置」を講師フォームと同一仕様で持つ
        assert 'data-field="secondary_placement"' in res.text
        static = client.get("/static/js/work_report_calc.js")
        assert static.status_code == 200
        assert "WorkReportCalc" in static.text
        # 副担当の位置の計算ルール（コマ後＝隙間のまま/コマ間＝隙間−副担当）は共通コアに集約されている
        assert "secondaryPlacementIsGap" in static.text
        assert "normalizedSecondaryPlacement" in static.text


class TestPhase2Api:
    def test_office_bulk_approve_moves_to_awaiting_sales(self, client, users):
        report_id = _create_report(client, users)
        for email, action in [
            ("p2-tutor@example.com", "submit"),
            ("p2-school@example.com", "approve"),
        ]:
            res = client.post(f"/api/w/reports/{report_id}/action", json={"action": action}, headers=_login(client, email))
            assert res.status_code == 200, res.text

        res = client.post(
            "/api/w/reports/bulk-action",
            json={"report_ids": [report_id], "action": "approve"},
            headers=_login(client, "p2-office@example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["updated"] == 1

        res = client.get(f"/api/w/reports/{report_id}", headers=_login(client, "p2-sales@example.com"))
        assert res.json()["status"] == WorkStatus.AWAITING_SALES

    def test_tutor_monthly_summary(self, client, users):
        _create_report(client, users)
        res = client.get(
            "/api/w/reports/monthly-summary?target_month=2026-06",
            headers=_login(client, "p2-tutor@example.com"),
        )
        assert res.status_code == 200
        data = res.json()
        assert data["total_reports"] == 1
        assert data["total_teach_minutes"] == 90
        assert data["total_break_minutes"] == 10
        assert data["total_commute_fee"] == 700
