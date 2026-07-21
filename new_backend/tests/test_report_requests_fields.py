"""要望連絡事項の3欄化（運営／講師／学校）の検証（改修依頼 202607211716-②）。

背景:
  従来の「要望連絡事項」は契約の担当業務設定（前期/後期の月時間・週コマ・適用期間＋契約期間）から
  自動生成される1欄だけだった。これを次の3欄へ増やし、担当ロールのみが入力・編集できるようにする。
    - 要望連絡事項（運営）  = meta.requests        契約管理で設定（自動生成テキストを流用・講師は読取専用）
    - 要望連絡事項（講師）  = meta.requests_tutor  業務連絡表で講師が入力
    - 要望連絡事項（学校）  = meta.requests_school 学校の「報告書を確認」で学校のみ入力

このテストで確認すること:
  1. 講師は requests_tutor を保存できる（既存の PATCH /api/w/reports/{id} で通る）。
  2. 講師は requests_school を書けない・消せない（新規作成では捨て、更新では保存済みの値を保持）。
  3. 学校は専用API PATCH /api/w/reports/{id}/school-requests で自校の報告書にのみ書ける。
     学校確認待ち以外は 409、他ロールは 403、他校の学校ユーザーは 403。
  4. 画面（講師フォーム・参照画面）とPDFフッターに3欄が出ている。

実メール送信ゼロ: conftest が MAIL_BACKEND=console を強制するため、提出・承認を含め実SMTP送信は起きない。
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile
from tests.conftest import TestSession

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def users(db):
    tutor = User(email="rq-tutor@example.com", role="tutor", roles=["tutor"], display_name="講師", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    school = User(email="rq-school@example.com", role="school", roles=["school"], display_name="学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    other_school = User(email="rq-school2@example.com", role="school", roles=["school"], display_name="別の学校", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    office = User(email="rq-office@example.com", role="office", roles=["office"], display_name="事務", allowed_systems=["new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, school, other_school, office])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="要望欄テスト生徒", system_type="new")
    db.add(assignment)
    db.flush()
    db.add(WorkAssignmentProfile(assignment_id=assignment.id, tutor_id=tutor.id, school_id=school.id, form_type="monthly_dispatch"))
    db.commit()
    return {"tutor": tutor, "school": school, "other_school": other_school, "office": office, "assignment": assignment}


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _create(client, users, meta=None):
    res = client.post("/api/w/reports", json={
        "assignment_id": str(users["assignment"].id),
        "target_month": "2026-06",
        "form_type": "monthly_dispatch",
        "form_data": {"lines": [{"date": "2026-06-05", "start": "09:00", "end": "10:00", "teach_minutes": 60}],
                      "meta": meta or {}},
    }, headers=_auth(client, "rq-tutor@example.com"))
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _submit(client, report_id):
    res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"},
                      headers=_auth(client, "rq-tutor@example.com"))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "awaiting_school"


class TestTutorRequestsField:
    def test_tutor_can_save_own_requests_field(self, client, users):
        """要望連絡事項（講師）は講師が保存・更新できる（既存のPATCHでそのまま通る）。"""
        report_id = _create(client, users, {"requests_tutor": "初回の記入"})
        res = client.patch(f"/api/w/reports/{report_id}", json={
            "form_data": {"lines": [], "meta": {"requests_tutor": "修正しました"}},
        }, headers=_auth(client, "rq-tutor@example.com"))
        assert res.status_code == 200, res.text
        assert res.json()["form_data"]["meta"]["requests_tutor"] == "修正しました"

    def test_tutor_cannot_seed_school_field_on_create(self, client, users):
        """講師は新規作成時に要望連絡事項（学校）を持ち込めない（学校欄のなりすまし防止）。"""
        report_id = _create(client, users, {"requests_school": "学校のふりをして記入"})
        res = client.get(f"/api/w/reports/{report_id}", headers=_auth(client, "rq-tutor@example.com"))
        assert res.status_code == 200, res.text
        assert "requests_school" not in res.json()["form_data"]["meta"]

    def test_tutor_patch_cannot_overwrite_or_erase_school_field(self, client, users):
        """学校が書いた要望連絡事項（学校）は、その後の講師の保存で上書きも消去もされない。

        講師フォームは meta を丸ごと組み立て直して送るため、サーバー側の保持が無いと消える。
        """
        report_id = _create(client, users)
        _submit(client, report_id)
        saved = client.patch(f"/api/w/reports/{report_id}/school-requests",
                             json={"requests_school": "学校からの連絡"},
                             headers=_auth(client, "rq-school@example.com"))
        assert saved.status_code == 200, saved.text
        # 学校が差戻し、講師が修正保存しても学校欄は保持される
        assert client.post(f"/api/w/reports/{report_id}/action", json={"action": "return", "comment": "修正してください"},
                           headers=_auth(client, "rq-school@example.com")).status_code == 200
        res = client.patch(f"/api/w/reports/{report_id}", json={
            "form_data": {"lines": [], "meta": {"requests_tutor": "直しました", "requests_school": "改変してみる"}},
        }, headers=_auth(client, "rq-tutor@example.com"))
        assert res.status_code == 200, res.text
        meta = res.json()["form_data"]["meta"]
        assert meta["requests_school"] == "学校からの連絡"
        assert meta["requests_tutor"] == "直しました"
        # meta ごと省略した保存でも消えない
        res = client.patch(f"/api/w/reports/{report_id}", json={
            "form_data": {"lines": [], "meta": {}},
        }, headers=_auth(client, "rq-tutor@example.com"))
        assert res.status_code == 200, res.text
        assert res.json()["form_data"]["meta"]["requests_school"] == "学校からの連絡"


class TestSchoolRequestsApi:
    def test_school_can_save_while_awaiting_school(self, client, users):
        """学校確認待ちの間、学校は自校の報告書に要望連絡事項（学校）を書ける。"""
        report_id = _create(client, users, {"requests_tutor": "講師の記入", "requests": "運営の記入"})
        _submit(client, report_id)
        res = client.patch(f"/api/w/reports/{report_id}/school-requests",
                           json={"requests_school": "  17時までに入室してください  "},
                           headers=_auth(client, "rq-school@example.com"))
        assert res.status_code == 200, res.text
        meta = res.json()["form_data"]["meta"]
        assert meta["requests_school"] == "17時までに入室してください"
        # 他の欄・明細には触れない
        assert meta["requests_tutor"] == "講師の記入"
        assert meta["requests"] == "運営の記入"
        assert len(res.json()["form_data"]["lines"]) == 1

    def test_school_cannot_save_before_submit(self, client, users):
        """下書き（学校確認待ちより前）は学校からは見えず、書けない。"""
        report_id = _create(client, users)
        res = client.patch(f"/api/w/reports/{report_id}/school-requests", json={"requests_school": "x"},
                           headers=_auth(client, "rq-school@example.com"))
        assert res.status_code == 403, res.text

    def test_school_cannot_save_after_approving(self, client, users):
        """承認してボールを離した後は編集できない（409）。"""
        report_id = _create(client, users)
        _submit(client, report_id)
        assert client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"},
                           headers=_auth(client, "rq-school@example.com")).status_code == 200
        res = client.patch(f"/api/w/reports/{report_id}/school-requests", json={"requests_school": "追記"},
                           headers=_auth(client, "rq-school@example.com"))
        assert res.status_code == 409, res.text
        assert "学校確認待ち" in res.json()["detail"]

    def test_other_school_is_rejected(self, client, users):
        """他校の学校ユーザーは書けない（403）。"""
        report_id = _create(client, users)
        _submit(client, report_id)
        res = client.patch(f"/api/w/reports/{report_id}/school-requests", json={"requests_school": "他校から"},
                           headers=_auth(client, "rq-school2@example.com"))
        assert res.status_code == 403, res.text

    @pytest.mark.parametrize("email", ["rq-tutor@example.com", "rq-office@example.com"])
    def test_non_school_roles_are_rejected(self, client, users, email):
        """講師・事務は学校欄を書けない（403＝その担当ロールのみ入力・編集できる）。"""
        report_id = _create(client, users)
        _submit(client, report_id)
        res = client.patch(f"/api/w/reports/{report_id}/school-requests", json={"requests_school": "別ロールから"},
                           headers=_auth(client, email))
        assert res.status_code == 403, res.text


class TestRequestsFieldsInViews:
    def test_tutor_form_has_three_labelled_fields(self, client, users):
        """講師フォームに3欄が並び、運営・学校の欄は講師側で読取専用にロックされる。"""
        _auth(client, "rq-tutor@example.com")
        html = client.get("/tutor/reports").text
        for label in ("要望連絡事項（運営）", "要望連絡事項（講師）", "要望連絡事項（学校）"):
            assert label in html
        assert 'data-meta="requests_tutor"' in html
        assert 'data-meta="requests_school"' in html
        # 運営欄は契約ロック、学校欄は他ロールのロック（別メッセージ）
        assert "'requests'" in html and "OTHER_ROLE_META_KEYS = ['requests_school']" in html
        assert "学校が入力する項目のため、講師側では変更できません" in html

    def test_report_view_shows_three_fields_and_school_editor(self, client, users):
        """参照画面に3欄が出て、学校欄だけ学校ロール・学校確認待ちのとき入力欄になる。"""
        report_id = _create(client, users)
        _auth(client, "rq-school@example.com")
        html = client.get(f"/reports/{report_id}/view").text
        for label in ("要望連絡事項（運営）", "要望連絡事項（講師）", "要望連絡事項（学校）"):
            assert label in html
        assert "meta.requests_tutor" in html
        assert "schoolRequestsFieldHtml" in html
        assert "/school-requests" in html
        # 編集できるのは学校ロール かつ 学校確認待ちのときだけ（対象月の条件は持たない＝案B）
        assert "ACTIVE_ROLE === 'school' && report.status === 'awaiting_school'" in html

    def test_pdf_footer_has_three_fields(self):
        """PDFフッターのラベルも3欄（運営・講師・学校）。"""
        from app.models.work import WorkReport
        from app.services.export_service import _report_footer_values

        report = WorkReport(target_month="2026-06", form_type="monthly_dispatch", status="approved",
                            form_data={"lines": [], "meta": {"requests": "運営", "requests_tutor": "講師", "requests_school": "学校"}})
        fields = dict(_report_footer_values(report))
        assert fields["要望連絡事項（運営）"] == "運営"
        assert fields["要望連絡事項（講師）"] == "講師"
        assert fields["要望連絡事項（学校）"] == "学校"


class TestNoRealMail:
    def test_environment_sends_no_real_mail(self):
        """テスト環境は MAIL_BACKEND!=smtp 固定＝提出・承認を含め実SMTP送信は起きない。"""
        from app.core.config import settings
        assert (settings.MAIL_BACKEND or "console").lower() != "smtp"
