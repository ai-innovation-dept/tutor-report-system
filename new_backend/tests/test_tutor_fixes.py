"""講師画面改修（下書き報告の削除・差戻し理由の公開）のテスト。"""
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkReport
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
        allowed_systems=["legacy", "new"] if role == "admin_master" else ["new"],
        password_hash=hash_password("Passw0rd!"),
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
    tutor = _add_user(db, "tutor@x.example.com", "tutor")
    school = _add_user(db, "school@x.example.com", "school")
    assignment = Assignment(
        tutor_id=tutor.id,
        parent_id=school.id,
        student_name="生徒A",
        system_type="new",
    )
    db.add(assignment)
    db.commit()
    return {"tutor": tutor, "school": school, "assignment": assignment}


def _create_report(client, assignment, headers):
    res = client.post(
        "/api/w/reports",
        json={
            "assignment_id": str(assignment.id),
            "target_month": "2026-06",
            "form_type": "monthly_dispatch",
            "form_data": {"lines": []},
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


class TestDeleteDraftReport:
    def test_tutor_can_delete_draft(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)

        res = client.delete(f"/api/w/reports/{report_id}", headers=headers)
        assert res.status_code == 204
        assert db.query(WorkReport).count() == 0

    def test_cannot_delete_after_submit(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers)
        assert res.status_code == 200

        res = client.delete(f"/api/w/reports/{report_id}", headers=headers)
        assert res.status_code == 409

    def test_can_delete_returned(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        school_headers = _auth(client, "school@x.example.com")
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "修正してください"},
            headers=school_headers,
        )
        assert res.status_code == 200 and res.json()["status"] == WorkStatus.RETURNED_TO_TUTOR

        res = client.delete(f"/api/w/reports/{report_id}", headers=tutor_headers)
        assert res.status_code == 204
        assert db.query(WorkReport).count() == 0

    def test_cannot_delete_awaiting_school(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)

        res = client.delete(f"/api/w/reports/{report_id}", headers=tutor_headers)
        assert res.status_code == 409

    def test_other_tutor_cannot_delete(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)
        _add_user(db, "tutor2@x.example.com", "tutor")

        res = client.delete(f"/api/w/reports/{report_id}", headers=_auth(client, "tutor2@x.example.com"))
        assert res.status_code == 403


class TestLastReturnComment:
    def test_return_comment_exposed_in_report(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        assert res.status_code == 200
        assert res.json()["status"] == WorkStatus.AWAITING_SCHOOL

        school_headers = _auth(client, "school@x.example.com")
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "時間が間違っています"},
            headers=school_headers,
        )
        assert res.status_code == 200, res.text

        res = client.get(f"/api/w/reports/{report_id}", headers=tutor_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == WorkStatus.RETURNED_TO_TUTOR
        assert body["last_return_comment"] == "時間が間違っています"

    def test_no_return_comment_for_draft(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)

        res = client.get(f"/api/w/reports/{report_id}", headers=headers)
        assert res.json()["last_return_comment"] is None


class TestReportNames:
    def test_report_exposes_school_and_tutor_names(self, client, db, setup):
        headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], headers)
        body = client.get(f"/api/w/reports/{report_id}", headers=headers).json()
        assert body["student_name"] == "生徒A"
        assert body["tutor_name"] == "tutorユーザー"
        assert body["school_name"] == "schoolユーザー"  # assignment.parent

    def test_school_approved_at_set_after_school_approve(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        # 学校承認前は未設定
        assert client.get(f"/api/w/reports/{report_id}", headers=tutor_headers).json()["school_approved_at"] is None
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "school@x.example.com"))
        body = client.get(f"/api/w/reports/{report_id}", headers=tutor_headers).json()
        assert body["school_approved_at"] is not None
        assert body["submitted_to_school_at"] is not None

    def test_approved_at_set_after_sales_approve(self, client, db, setup):
        # 営業承認で完了（経理ステップ廃止）。approved_at は営業の最終承認で設定される
        tutor_headers = _auth(client, "tutor@x.example.com")
        _add_user(db, "office-approved@x.example.com", "office")
        _add_user(db, "sales-approved@x.example.com", "sales")
        report_id = _create_report(client, setup["assignment"], tutor_headers)

        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "school@x.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "office-approved@x.example.com"))
        before_sales = client.get(f"/api/w/reports/{report_id}", headers=tutor_headers).json()
        assert before_sales["status"] == WorkStatus.AWAITING_SALES
        assert before_sales["approved_at"] is None

        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "sales-approved@x.example.com"))
        body = client.get(f"/api/w/reports/{report_id}", headers=tutor_headers).json()
        assert body["status"] == WorkStatus.APPROVED
        assert body["approved_at"] is not None

    def test_school_name_falls_back_to_meta(self, client, db):
        # parent 未設定の紐付けでは meta.dispatch_place_name を学校名に使う
        tutor = _add_user(db, "t9@x.example.com", "tutor")
        assignment = Assignment(tutor_id=tutor.id, student_name="生徒Z", system_type="new")
        db.add(assignment)
        db.commit()
        headers = _auth(client, "t9@x.example.com")
        res = client.post(
            "/api/w/reports",
            json={
                "assignment_id": str(assignment.id),
                "target_month": "2026-06",
                "form_type": "monthly_dispatch",
                "form_data": {"lines": [], "meta": {"dispatch_place_name": "メタ高校"}},
            },
            headers=headers,
        )
        assert res.json()["school_name"] == "メタ高校"

    def test_assignment_export_filename_uses_school_name(self, client, db):
        tutor = _add_user(db, "pdf-tutor@x.example.com", "tutor")
        school = _add_user(db, "pdf-school@x.example.com", "school")
        school.display_name = "椅子戸学園"
        assignment = Assignment(
            tutor_id=tutor.id,
            parent_id=school.id,
            student_name="生徒一郎",
            system_type="new",
        )
        db.add(assignment)
        db.commit()
        headers = _auth(client, "pdf-tutor@x.example.com")
        res = client.post(
            "/api/w/reports",
            json={
                "assignment_id": str(assignment.id),
                "target_month": "2026-06",
                "form_type": "monthly_dispatch",
                "form_data": {"lines": [{"date": "2026-06-01", "teach_minutes": 60}]},
            },
            headers=headers,
        )
        report_id = res.json()["id"]
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "pdf-school@x.example.com"))
        _add_user(db, "pdf-office@x.example.com", "office")
        _add_user(db, "pdf-sales@x.example.com", "sales")
        _add_user(db, "pdf-master@x.example.com", "admin_master")
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "pdf-office@x.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "pdf-sales@x.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "pdf-master@x.example.com"))

        export = client.get(
            f"/api/w/reports/export?target_month=2026-06&assignment_id={assignment.id}",
            headers=headers,
        )

        disposition = unquote(export.headers["content-disposition"])
        assert export.status_code == 200
        assert "椅子戸学園" in disposition
        assert "生徒一郎" not in disposition


class TestAssignmentForSchool:
    def test_tutor_creates_assignment_by_school(self, client, db):
        tutor = _add_user(db, "t1@x.example.com", "tutor")
        school = _add_user(db, "sc@x.example.com", "school")
        headers = _auth(client, "t1@x.example.com")

        res = client.post("/api/w/assignments/for-school", json={"school_id": str(school.id)}, headers=headers)
        assert res.status_code == 200, res.text
        first = res.json()
        assert first["parent_id"] == str(school.id)
        assert first["tutor_id"] == str(tutor.id)

        # 同じ学校なら同じ紐付けを返す（重複作成しない）
        res2 = client.post("/api/w/assignments/for-school", json={"school_id": str(school.id)}, headers=headers)
        assert res2.json()["id"] == first["id"]

    def test_rejects_non_school_target(self, client, db):
        _add_user(db, "t1@x.example.com", "tutor")
        other = _add_user(db, "o1@x.example.com", "office")
        headers = _auth(client, "t1@x.example.com")
        res = client.post("/api/w/assignments/for-school", json={"school_id": str(other.id)}, headers=headers)
        assert res.status_code == 422

    def test_report_creatable_after_for_school(self, client, db):
        _add_user(db, "t1@x.example.com", "tutor")
        school = _add_user(db, "sc@x.example.com", "school")
        headers = _auth(client, "t1@x.example.com")
        assignment = client.post("/api/w/assignments/for-school", json={"school_id": str(school.id)}, headers=headers).json()
        res = client.post(
            "/api/w/reports",
            json={
                "assignment_id": assignment["id"],
                "target_month": "2026-06",
                "form_type": "monthly_dispatch",
                "form_data": {"lines": []},
            },
            headers=headers,
        )
        assert res.status_code == 201, res.text


class TestApprovedReturn:
    def _advance_to_approved(self, client, db, setup, tutor_headers):
        _add_user(db, "office@x.example.com", "office")
        _add_user(db, "sales@x.example.com", "sales")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        # tutor -> school -> office -> sales（営業承認で完了）
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "school@x.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "office@x.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "sales@x.example.com"))
        assert client.get(f"/api/w/reports/{report_id}", headers=tutor_headers).json()["status"] == WorkStatus.APPROVED
        return report_id

    def test_sales_returns_approved_report(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = self._advance_to_approved(client, db, setup, tutor_headers)

        # 完了からの差戻し（営業が最終承認者）
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "金額の修正をお願いします"},
            headers=_auth(client, "sales@x.example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.RETURNED_TO_OFFICE

    def test_return_approved_requires_comment(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = self._advance_to_approved(client, db, setup, tutor_headers)

        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "   "},
            headers=_auth(client, "sales@x.example.com"),
        )
        assert res.status_code == 422


class TestStaffSeeAllReports:
    def test_office_sees_report_after_it_moves_to_sales(self, client, db, setup):
        """事務が承認して営業確認待ちへ移っても、事務の画面（全件取得）で引き続き見える。"""
        tutor_headers = _auth(client, "tutor@x.example.com")
        _add_user(db, "office@x.example.com", "office")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "school@x.example.com"))

        office_headers = _auth(client, "office@x.example.com")
        # 事務確認待ちの段階で見える
        listed = client.get("/api/w/reports", headers=office_headers).json()
        assert report_id in [r["id"] for r in listed]
        # 事務が承認 → 営業確認待ちへ
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=office_headers)
        assert res.json()["status"] == WorkStatus.AWAITING_SALES
        # 移動後も事務の一覧に残る（パイプラインで次列へ動いて見える）
        listed = client.get("/api/w/reports", headers=office_headers).json()
        assert report_id in [r["id"] for r in listed]

    def test_sales_sees_draft_reports(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        _add_user(db, "sales@x.example.com", "sales")
        report_id = _create_report(client, setup["assignment"], tutor_headers)  # draft
        listed = client.get("/api/w/reports", headers=_auth(client, "sales@x.example.com")).json()
        assert report_id in [r["id"] for r in listed]


class TestOfficeHandlesReturnedToOffice:
    def _advance_to_returned_to_office(self, client, db, setup):
        """tutor提出→学校承認→事務承認→営業差戻し で returned_to_office まで進める。"""
        tutor_headers = _auth(client, "tutor@x.example.com")
        _add_user(db, "office@x.example.com", "office")
        _add_user(db, "sales@x.example.com", "sales")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "school@x.example.com"))
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=_auth(client, "office@x.example.com"))
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "営業から事務へ差戻し"},
            headers=_auth(client, "sales@x.example.com"),
        )
        assert res.status_code == 200 and res.json()["status"] == WorkStatus.RETURNED_TO_OFFICE
        return report_id

    def test_office_approves_forward_to_sales(self, client, db, setup):
        report_id = self._advance_to_returned_to_office(client, db, setup)
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "approve"},
            headers=_auth(client, "office@x.example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_SALES

    def test_office_returns_to_tutor(self, client, db, setup):
        report_id = self._advance_to_returned_to_office(client, db, setup)
        res = client.post(
            f"/api/w/reports/{report_id}/action",
            json={"action": "return", "comment": "講師に修正依頼"},
            headers=_auth(client, "office@x.example.com"),
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.RETURNED_TO_TUTOR


class TestSchoolVisibility:
    def test_school_sees_reports_across_statuses(self, client, db, setup):
        tutor_headers = _auth(client, "tutor@x.example.com")
        report_id = _create_report(client, setup["assignment"], tutor_headers)
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "submit"}, headers=tutor_headers)

        school_headers = _auth(client, "school@x.example.com")
        listed = client.get("/api/w/reports", headers=school_headers).json()
        assert [r["id"] for r in listed] == [report_id]

        # 承認後も自校の報告として参照できる
        client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=school_headers)
        listed = client.get("/api/w/reports", headers=school_headers).json()
        assert report_id in [r["id"] for r in listed]
        assert listed[0]["status"] == WorkStatus.AWAITING_OFFICE
