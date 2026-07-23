# === 指導月報 テスト START ===
"""指導月報（monthly_reports）のテスト。

- 講師の作成・更新（承認依頼前のみ）と正規化
- 承認依頼ガード（月報未作成・必須項目未入力なら 422）
- 保護者承認時の保護者記入欄（月報がある月は必須・承認と同時に保存）
- 指導月報PDF（/api/reports/export-monthly）の権限・出力
"""
from datetime import date, time

from app.core.security import hash_password
from app.core.time import get_current_jst_date, get_current_jst_month
from app.models import Assignment, LessonReport, MonthlyReport, ReportStatus, User
from tests.conftest import seed_monthly_report, token


def _auth(token_value):
    return {"Authorization": f"Bearer {token_value}"}


def _create_report(client, tutor_token, assignment):
    res = client.post("/api/reports", headers=_auth(tutor_token), json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(get_current_jst_date()),
        "start_time": "18:00",
        "end_time": "19:00",
        "subject": "算数",
        "content": "指導内容",
    })
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _delete_monthly(db):
    db.query(MonthlyReport).delete()
    db.commit()


def test_tutor_can_upsert_monthly_report_and_overview(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    _delete_monthly(db)

    payload = {
        "grade": "小学5年",
        "form_data": {
            "issues": ["計算ミスを減らす", "音読の習慣", "", "", ""],
            "target_schools": ["A中学校", "", "", "B中学校", ""],
            "test_mock": {"name": "全国模試", "exam_month": 6, "exam_day": 15,
                          "scores": [{"score": "80", "deviation": "58"}]},
            "test_school": {"term": "1", "term_type": "中間", "scores": []},
            "lesson_days": [3, 10, "17", 40],
            "next_month_plan_days": [7, 14],
            "total_hours": "12.5",
            "retrospect": {
                "late": {"answer": "B", "count": "1", "informed": "a"},
                "schedule_change": {"answer": "A", "count": "", "informed": ""},
                "change_reason": {"answer": "B", "reason": "大学試験のため"},
                "makeup": {"answer": "C", "plans": [{"from_month": 7, "from_day": 1, "to_month": 7, "to_day": 8}]},
            },
            "notes": "来月もよろしくお願いします。",
            "unknown_key": "dropped",
        },
    }
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token), json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["grade"] == "小学5年"
    # 正規化: 未知キー破棄・日付は範囲内のみ・数値文字列は int 化
    assert "unknown_key" not in body["form_data"]
    assert body["form_data"]["lesson_days"] == [3, 10, 17]
    assert body["form_data"]["retrospect"]["makeup"]["plans"] == [
        {"from_month": 7, "from_day": 1, "to_month": 7, "to_day": 8}
    ]
    assert body["form_data"]["test_mock"]["scores"][0] == {"score": "80", "deviation": "58"}

    # 更新（同一 担当×月 は1件のまま）
    payload["grade"] = "小6"
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token), json=payload)
    assert res.status_code == 200
    assert db.query(MonthlyReport).count() == 1

    overview = client.get(f"/api/monthly-reports/overview?target_month={month}", headers=_auth(tutor_token))
    assert overview.status_code == 200
    data = overview.json()
    assert data["mock_subjects"] == ["国語", "算数", "2科", "社会", "理科", "4科"]
    assert data["school_subjects"] == ["英語", "算数", "国語", "社会", "理科"]
    item = data["assignments"][0]
    assert item["editable"] is True
    assert item["report"]["grade"] == "小6"


def test_monthly_report_upsert_denied_for_other_tutor_and_parent(client, db):
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    other_tutor = User(email="tutor2@example.com", role="tutor", roles=["tutor"], display_name="Tutor 2",
                       allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    db.add(other_tutor)
    db.commit()
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}",
                     headers=_auth(token(client, "tutor2@example.com")), json={"grade": "小5", "form_data": {}})
    assert res.status_code == 403
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}",
                     headers=_auth(token(client, "parent@example.com")), json={"grade": "小5", "form_data": {}})
    assert res.status_code == 403


def test_submit_blocked_until_monthly_report_ready(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    rid = _create_report(client, tutor_token, assignment)

    # 月報なし → 承認依頼できない
    _delete_monthly(db)
    res = client.post(f"/api/reports/{rid}/submit-to-parent", headers=_auth(tutor_token), json={})
    assert res.status_code == 422
    assert "指導月報が未作成" in res.json()["detail"]

    # 問題点と対策なし → 不可（一括APIも同じガードを通る）
    client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token),
               json={"grade": "小5", "form_data": {"issues": ["", "", "", "", ""]}})
    res = client.post(f"/api/reports/{rid}/submit-to-parent", headers=_auth(tutor_token), json={})
    assert res.status_code == 422
    assert "問題点と対策" in res.json()["detail"]

    # 学年が未入力でも、問題点と対策があれば承認依頼できる（学年ほか他項目は任意＝改修 202607231755 ④）
    client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token),
               json={"grade": "", "form_data": {"issues": ["対策あり"]}})
    res = client.post("/api/reports/submit-to-parent-bulk", headers=_auth(tutor_token),
                      json={"report_ids": [rid], "target_month": month})
    assert res.status_code == 200, res.text


def _month_before(month: str) -> str:
    year, month_num = map(int, month.split("-"))
    return f"{year - 1}-12" if month_num == 1 else f"{year}-{month_num - 1:02d}"


def test_overview_inherits_previous_target_schools(client, db):
    """志望校の引継ぎ（改修 202607231755 ①）。

    月報が未作成の月は、直近の過去月報の「現時点での志望校」をデフォルト表示用に返す。
    当月の月報を作成済みなら引継ぎ値は返さない（保存済みの値を使う）。
    """
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    prev_month = _month_before(month)
    older_month = _month_before(prev_month)
    _delete_monthly(db)

    # さらに古い月報（別の志望校）も置き、「直近の過去月」を引き継ぐことを確認する
    db.add(MonthlyReport(assignment_id=assignment.id, tutor_id=assignment.tutor_id, parent_id=assignment.parent_id,
                         target_month=older_month, grade="小4",
                         form_data={"target_schools": ["旧A中学校", "", "", "", ""]}))
    db.add(MonthlyReport(assignment_id=assignment.id, tutor_id=assignment.tutor_id, parent_id=assignment.parent_id,
                         target_month=prev_month, grade="小5",
                         form_data={"target_schools": ["A中学校", "B中学校", "", "", ""]}))
    db.commit()

    overview = client.get(f"/api/monthly-reports/overview?target_month={month}", headers=_auth(tutor_token))
    assert overview.status_code == 200, overview.text
    item = overview.json()["assignments"][0]
    assert item["report"] is None
    assert item["previous_target_schools"][:2] == ["A中学校", "B中学校"]

    # 当月の月報を作成すると引継ぎ値は返さない
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token),
                     json={"grade": "小5", "form_data": {"issues": ["対策"], "target_schools": ["C中学校"]}})
    assert res.status_code == 200, res.text
    overview = client.get(f"/api/monthly-reports/overview?target_month={month}", headers=_auth(tutor_token))
    item = overview.json()["assignments"][0]
    assert item["previous_target_schools"] is None
    assert item["report"]["form_data"]["target_schools"][0] == "C中学校"


def test_monthly_report_locked_after_submit_and_editable_after_return(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    rid = _create_report(client, tutor_token, assignment)

    assert client.post(f"/api/reports/{rid}/submit-to-parent", headers=_auth(tutor_token), json={}).status_code == 200
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token),
                     json={"grade": "小5", "form_data": {"issues": ["更新"]}})
    assert res.status_code == 409
    assert "承認依頼済み" in res.json()["detail"]

    # 差戻しで報告書が講師へ戻れば再び編集できる
    returned = client.post(f"/api/reports/{rid}/parent-return", headers=_auth(parent_token), json={"comment": "修正してください"})
    assert returned.status_code == 200
    res = client.put(f"/api/monthly-reports/{assignment.id}/{month}", headers=_auth(tutor_token),
                     json={"grade": "小5", "form_data": {"issues": ["更新後"]}})
    assert res.status_code == 200


def test_parent_approve_requires_parent_note_when_monthly_exists(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    # 保護者記入欄が未記入の月報にする
    _delete_monthly(db)
    seed_monthly_report(db, assignment, parent_note=None)
    rid = _create_report(client, tutor_token, assignment)
    assert client.post(f"/api/reports/{rid}/submit-to-parent", headers=_auth(tutor_token), json={}).status_code == 200

    # 記入なしでは承認できない（単件・一括とも）
    res = client.post(f"/api/reports/{rid}/parent-approve", headers=_auth(parent_token), json={})
    assert res.status_code == 422
    assert "保護者記入欄" in res.json()["detail"]
    res = client.post("/api/reports/parent-approve-bulk", headers=_auth(parent_token),
                      json={"report_ids": [rid], "target_month": month})
    assert res.status_code == 422

    # 記入すれば承認でき、月報へ保存される
    res = client.post("/api/reports/parent-approve-bulk", headers=_auth(parent_token),
                      json={"report_ids": [rid], "target_month": month, "parent_note": "いつもありがとうございます。"})
    assert res.status_code == 200, res.text
    db.expire_all()
    monthly = db.query(MonthlyReport).one()
    assert monthly.parent_note == "いつもありがとうございます。"
    assert monthly.parent_note_at is not None
    assert monthly.parent_note_by is not None


def test_parent_approve_without_monthly_keeps_legacy_behavior(client, db):
    # 月報が存在しない月（機能リリース前に提出済みの月など）は従来どおり記入なしで承認できる
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    _delete_monthly(db)
    today = get_current_jst_date()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="legacy month",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.awaiting_parent_approval.value,
    )
    db.add(report)
    db.commit()
    res = client.post(f"/api/reports/{report.id}/parent-approve", headers=_auth(parent_token), json={})
    assert res.status_code == 200, res.text


def test_monthly_report_list_scopes_by_role(client, db):
    month = get_current_jst_month()
    other_parent = User(email="other-parent@example.com", role="parent", roles=["parent"], display_name="Other",
                        allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    db.add(other_parent)
    db.commit()

    parent_res = client.get(f"/api/monthly-reports?target_month={month}", headers=_auth(token(client, "parent@example.com")))
    assert parent_res.status_code == 200
    assert len(parent_res.json()) == 1
    assert parent_res.json()[0]["grade"] == "小5"

    other_res = client.get(f"/api/monthly-reports?target_month={month}", headers=_auth(token(client, "other-parent@example.com")))
    assert other_res.status_code == 200
    assert other_res.json() == []

    # target_month 省略時は全月分（存在チェック用）
    master_res = client.get("/api/monthly-reports", headers=_auth(token(client, "master@example.com")))
    assert master_res.status_code == 200
    assert len(master_res.json()) == 1


def _make_approved_report(db, assignment, *, with_parent_approval=True):
    from datetime import datetime, timezone

    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="approved",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
        parent_approved_at=datetime.now(timezone.utc) if with_parent_approval else None,
    )
    db.add(report)
    db.commit()
    return report


def test_export_monthly_pdf_for_tutor_and_admin(client, db):
    tutor_token = token(client, "tutor@example.com")
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    _make_approved_report(db, assignment)

    res = client.get(f"/api/reports/export-monthly?target_month={month}&assignment_id={assignment.id}",
                     headers=_auth(tutor_token))
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert "content-disposition" in res.headers

    res = client.get(f"/api/reports/export-monthly?target_month={month}&scope=approved_only",
                     headers=_auth(master_token))
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")


def test_export_monthly_pdf_404_when_monthly_missing(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    _make_approved_report(db, assignment)
    _delete_monthly(db)

    res = client.get(f"/api/reports/export-monthly?target_month={month}&assignment_id={assignment.id}",
                     headers=_auth(tutor_token))
    assert res.status_code == 404
    assert "指導月報" in res.json()["detail"]


def test_export_monthly_pdf_denied_for_other_parent(client, db):
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    _make_approved_report(db, assignment)
    other_parent = User(email="other-parent@example.com", role="parent", roles=["parent"], display_name="Other",
                        allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    db.add(other_parent)
    db.commit()
    res = client.get(f"/api/reports/export-monthly?target_month={month}&assignment_id={assignment.id}",
                     headers=_auth(token(client, "other-parent@example.com")))
    assert res.status_code == 403


def test_export_monthly_pdf_tutor_requires_final_approval(client, db):
    # 講師・保護者のPDFは最終承認済みの報告書がある月のみ（/export・/export-daily と同一の対象選定）
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    month = get_current_jst_month()
    rid = _create_report(client, tutor_token, assignment)
    assert rid
    res = client.get(f"/api/reports/export-monthly?target_month={month}&assignment_id={assignment.id}",
                     headers=_auth(tutor_token))
    assert res.status_code == 404
# === 指導月報 テスト END ===
