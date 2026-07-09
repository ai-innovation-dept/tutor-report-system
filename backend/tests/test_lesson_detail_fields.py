"""指導報告の内容項目 再構築（教科ほか7項目化）のテスト。

- 追加項目（教科/使用教材/何を指導したか/学習状況/宿題状況/次回宿題/次回予定）の作成・取得・更新。
- 宿題状況は A/B/C のみ許可。次回の予定/指導日・開始時刻は任意（未指定可）。
実メールは送らない（conftest で MAIL_BACKEND=console）。
"""
from uuid import UUID

from app.core.time import get_current_jst_date
from app.models import Assignment, LessonReport
from tests.conftest import token


def _headers(client, email="tutor@example.com"):
    return {"Authorization": f"Bearer {token(client, email)}"}


def _full_payload(assignment_id, lesson_date):
    return {
        "assignment_id": str(assignment_id),
        "lesson_date": str(lesson_date),
        "start_time": "18:00",
        "end_time": "19:00",
        "break_minutes": 0,
        "grade_level": "中",                        # 学年区分（小/中/高）
        "grade_year": 2,                            # 学年数
        "subject": "数学",                          # 教科
        "material_name": "青チャート p.20〜25",      # (a)
        "content": "二次関数（平方完成）",            # (b)
        "learning_status": "平方完成でつまずき。反復で対策する。",  # (c)
        "homework_status": "B",                     # (d)
        "next_homework": "章末問題 1〜5",
        "next_lesson_date": str(lesson_date),
        "next_lesson_start": "18:30",
    }


def test_create_report_with_detail_fields(client, db):
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    res = client.post("/api/reports", headers=_headers(client), json=_full_payload(assignment.id, today))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["grade_level"] == "中"
    assert body["grade_year"] == 2
    assert body["subject"] == "数学"
    assert body["material_name"] == "青チャート p.20〜25"
    assert body["content"] == "二次関数（平方完成）"
    assert body["learning_status"].startswith("平方完成")
    assert body["homework_status"] == "B"
    assert body["next_homework"] == "章末問題 1〜5"
    assert body["next_lesson_date"] == str(today)
    assert str(body["next_lesson_start"]).startswith("18:30")
    # DB にも保存されている
    report = db.query(LessonReport).filter(LessonReport.id == UUID(body["id"])).one()
    assert report.grade_level == "中"
    assert report.grade_year == 2
    assert report.material_name == "青チャート p.20〜25"
    assert report.homework_status == "B"
    assert report.next_lesson_start is not None


def test_next_lesson_is_optional(client, db):
    """次回の予定/指導日・開始時刻は未指定（null）でも作成できる。"""
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    payload = _full_payload(assignment.id, today)
    payload["next_lesson_date"] = None
    payload["next_lesson_start"] = None
    res = client.post("/api/reports", headers=_headers(client), json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["next_lesson_date"] is None
    assert body["next_lesson_start"] is None


def test_invalid_homework_status_rejected(client, db):
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    payload = _full_payload(assignment.id, today)
    payload["homework_status"] = "X"
    res = client.post("/api/reports", headers=_headers(client), json=payload)
    assert res.status_code == 422


def test_grade_is_optional_at_api(client, db):
    """学年の必須化は入力UI（フロント required）で行うため、API/スキーマ層は未指定でも作成できる（既存互換）。"""
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    payload = _full_payload(assignment.id, today)
    payload.pop("grade_level")
    payload.pop("grade_year")
    res = client.post("/api/reports", headers=_headers(client), json=payload)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["grade_level"] is None
    assert body["grade_year"] is None


def test_invalid_grade_year_rejected(client, db):
    """学年数は 1〜6 の範囲のみ許可（範囲外は 422）。"""
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    payload = _full_payload(assignment.id, today)
    payload["grade_year"] = 7
    res = client.post("/api/reports", headers=_headers(client), json=payload)
    assert res.status_code == 422


def test_invalid_grade_level_rejected(client, db):
    """学年区分は 小/中/高 のみ許可（それ以外は 422）。"""
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    payload = _full_payload(assignment.id, today)
    payload["grade_level"] = "大"
    res = client.post("/api/reports", headers=_headers(client), json=payload)
    assert res.status_code == 422


def test_patch_updates_detail_fields(client, db):
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    created = client.post("/api/reports", headers=_headers(client), json=_full_payload(assignment.id, today))
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    res = client.patch(f"/api/reports/{rid}", headers=_headers(client), json={
        "grade_level": "高",
        "grade_year": 3,
        "learning_status": "理解が進み、応用に入れる。",
        "homework_status": "A",
        "material_name": "新しい教材",
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["grade_level"] == "高"
    assert body["grade_year"] == 3
    assert body["learning_status"] == "理解が進み、応用に入れる。"
    assert body["homework_status"] == "A"
    assert body["material_name"] == "新しい教材"
    # 変更していない項目は保持される
    assert body["content"] == "二次関数（平方完成）"


def test_list_reports_include_detail_fields(client, db):
    """一覧APIでも新項目が返る（参照画面が読み取れること）。"""
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    client.post("/api/reports", headers=_headers(client), json=_full_payload(assignment.id, today))
    res = client.get("/api/reports", headers=_headers(client))
    assert res.status_code == 200, res.text
    rows = res.json()
    assert rows and "material_name" in rows[0] and "homework_status" in rows[0]
