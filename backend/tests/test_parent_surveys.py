# === 保護者アンケート テスト START（改修 202607231755 ③） ===
"""保護者アンケート（parent_surveys）のテスト。

- 保護者本人の回答の作成・更新・取得（未回答は null・指導月報×1件）
- 閲覧は運営スタッフのみ（講師は自分への評価も一切見られない・保護者も一覧は不可）
- バリデーション（5段階評価は1〜5・継続意向は3択）
- 回答は任意＝アンケート未回答でも保護者承認できる（既存フローテストが担保）
- /admin/surveys ページの到達権限（運営4ロールのみ）
"""
from app.core.security import hash_password
from app.models import MonthlyReport, ParentSurvey, User
from tests.conftest import token


def _auth(token_value):
    return {"Authorization": f"Bearer {token_value}"}


VALID_ANSWER = {
    "q_satisfaction": 5,
    "q_clarity": 4,
    "q_communication": 5,
    "q_motivation": 4,
    "q_punctuality": 5,
    "q_continuation": "continue",
    "comment": "丁寧に教えていただいています。",
}


def _monthly(db):
    return db.query(MonthlyReport).one()


def test_parent_can_answer_and_update_survey(client, db):
    parent_token = token(client, "parent@example.com")
    monthly = _monthly(db)

    # 未回答なら null
    res = client.get(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token))
    assert res.status_code == 200, res.text
    assert res.json() is None

    # 回答（作成）
    res = client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token), json=VALID_ANSWER)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["q_satisfaction"] == 5
    assert body["q_continuation"] == "continue"
    assert body["comment"] == "丁寧に教えていただいています。"
    assert body["target_month"] == monthly.target_month

    # 再取得できる（自分の回答）
    res = client.get(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token))
    assert res.status_code == 200
    assert res.json()["q_clarity"] == 4

    # 更新しても 月報×1件 のまま（コメントは空なら null 保存）
    res = client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                     json={**VALID_ANSWER, "q_satisfaction": 3, "q_continuation": "neutral", "comment": "  "})
    assert res.status_code == 200, res.text
    assert res.json()["q_satisfaction"] == 3
    assert res.json()["comment"] is None
    assert db.query(ParentSurvey).count() == 1


def test_survey_hidden_from_tutor_and_other_parent(client, db):
    parent_token = token(client, "parent@example.com")
    monthly = _monthly(db)
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                      json=VALID_ANSWER).status_code == 200

    # 講師は取得・回答・一覧すべて不可（自分への評価も見られない）
    tutor_token = token(client, "tutor@example.com")
    assert client.get(f"/api/parent-surveys/{monthly.id}", headers=_auth(tutor_token)).status_code == 403
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(tutor_token),
                      json=VALID_ANSWER).status_code == 403
    assert client.get("/api/parent-surveys", headers=_auth(tutor_token)).status_code == 403

    # 他の保護者は対象の月報に触れない（存在も知らせない404）・保護者は一覧不可
    other = User(email="other-parent@example.com", role="parent", roles=["parent"], display_name="Other",
                 allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    db.add(other)
    db.commit()
    other_token = token(client, "other-parent@example.com")
    assert client.get(f"/api/parent-surveys/{monthly.id}", headers=_auth(other_token)).status_code == 404
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(other_token),
                      json=VALID_ANSWER).status_code == 404
    assert client.get("/api/parent-surveys", headers=_auth(parent_token)).status_code == 403


def test_admin_can_list_and_filter_surveys(client, db):
    parent_token = token(client, "parent@example.com")
    monthly = _monthly(db)
    month = monthly.target_month
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                      json=VALID_ANSWER).status_code == 200

    for email in ["receiver@example.com", "reviewer@example.com", "master@example.com"]:
        res = client.get("/api/parent-surveys", headers=_auth(token(client, email)))
        assert res.status_code == 200, f"{email}: {res.text}"
        rows = res.json()
        assert len(rows) == 1
        assert rows[0]["student_name"] == "Student"
        assert rows[0]["tutor_name"] == "Tutor"
        assert rows[0]["parent_name"] == "Parent"
        assert rows[0]["q_satisfaction"] == 5

    admin_token = token(client, "receiver@example.com")
    tutor_id = rows[0]["tutor_id"]
    assert len(client.get(f"/api/parent-surveys?tutor_id={tutor_id}", headers=_auth(admin_token)).json()) == 1
    assert len(client.get(f"/api/parent-surveys?month_from={month}&month_to={month}",
                          headers=_auth(admin_token)).json()) == 1
    assert client.get("/api/parent-surveys?month_to=2000-01", headers=_auth(admin_token)).json() == []


def test_survey_validation_rejects_out_of_range(client, db):
    parent_token = token(client, "parent@example.com")
    monthly = _monthly(db)
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                      json={**VALID_ANSWER, "q_satisfaction": 0}).status_code == 422
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                      json={**VALID_ANSWER, "q_motivation": 6}).status_code == 422
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                      json={**VALID_ANSWER, "q_continuation": "maybe"}).status_code == 422
    incomplete = {key: value for key, value in VALID_ANSWER.items() if key != "q_clarity"}
    assert client.put(f"/api/parent-surveys/{monthly.id}", headers=_auth(parent_token),
                      json=incomplete).status_code == 422


def test_admin_surveys_page_access(client, db):
    # 運営はページに到達できる（集計画面の主要要素を含む）
    token(client, "receiver@example.com")
    html = client.get("/admin/surveys").text
    assert "保護者アンケート（講師評価）" in html
    assert 'id="questionCharts"' in html

    # 講師・保護者はログイン画面へ戻される（アンケートページに到達できない）
    token(client, "tutor@example.com")
    res = client.get("/admin/surveys", follow_redirects=False)
    assert res.status_code == 302
    token(client, "parent@example.com")
    res = client.get("/admin/surveys", follow_redirects=False)
    assert res.status_code == 302
# === 保護者アンケート テスト END ===
