"""担当管理のCSVエクスポート/インポートの統合テスト（既存システム=legacy）。

照合キー=(講師No, 生徒名)。一致する担当があれば保護者の紐づけを上書き更新、無ければ新規作成。
保護者No 空欄=未設定／記入かつ該当する保護者が居ない=エラー。講師Noは既存講師が必須。
1件でもエラーなら全件中止。
"""
import csv
import io

from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import Assignment, User
from app.services import assignment_import_service as ais
from tests.conftest import token


def _headers(client, email):
    return {"Authorization": f"Bearer {token(client, email)}"}


def _make_user(email, user_no, role="tutor", name="氏名"):
    db = SessionLocal()
    try:
        u = User(
            email=email,
            role=role,
            roles=[role],
            display_name=name,
            password_hash=hash_password("Passw0rd!"),
            user_no=user_no,
            tutor_no=user_no if role == "tutor" else None,
            is_active=True,
            allowed_systems=["legacy"],
        )
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _make_assignment(tutor_id, student_name, parent_id=None):
    db = SessionLocal()
    try:
        a = Assignment(tutor_id=tutor_id, student_name=student_name, parent_id=parent_id, is_active=True)
        db.add(a)
        db.commit()
        return a.id
    finally:
        db.close()


def _get_assignment(tutor_id, student_name):
    db = SessionLocal()
    try:
        return db.scalar(select(Assignment).where(Assignment.tutor_id == tutor_id, Assignment.student_name == student_name))
    finally:
        db.close()


def _csv_bytes(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ais.headers())
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in ais.headers()})
    return buf.getvalue().encode("utf-8-sig")


def _upload(client, email, rows_or_bytes):
    data = rows_or_bytes if isinstance(rows_or_bytes, (bytes, bytearray)) else _csv_bytes(rows_or_bytes)
    return client.post(
        "/api/assignments/import",
        files={"file": ("assignments.csv", data, "text/csv")},
        headers=_headers(client, email),
    )


# --- エクスポート ---

def test_export_returns_csv_with_assignment(client):
    tutor_id = _make_user("t1@x.com", "10001", role="tutor", name="講師太郎")
    parent_id = _make_user("p1@x.com", "20001", role="parent", name="保護者花子")
    _make_assignment(tutor_id, "生徒花子", parent_id)
    res = client.get("/api/assignments/export", headers=_headers(client, "master@example.com"))
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"\xef\xbb\xbf")
    reader = csv.DictReader(io.StringIO(res.content.decode("utf-8-sig")))
    assert reader.fieldnames == ais.headers()
    rows = list(reader)
    target = [r for r in rows if r[ais.STUDENT_NAME] == "生徒花子"]
    assert target and target[0][ais.TUTOR_NO] == "10001" and target[0][ais.PARENT_NO] == "20001"


def test_export_forbidden_for_tutor(client):
    res = client.get("/api/assignments/export", headers=_headers(client, "tutor@example.com"))
    assert res.status_code == 403


# --- 新規作成 ---

def test_import_creates_assignment_with_parent(client):
    tutor_id = _make_user("t2@x.com", "10002", role="tutor")
    parent_id = _make_user("p2@x.com", "20002", role="parent")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10002", ais.STUDENT_NAME: "新太郎", ais.PARENT_NO: "20002"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    a = _get_assignment(tutor_id, "新太郎")
    assert a is not None and a.parent_id == parent_id and a.is_active is True


def test_import_creates_assignment_without_parent(client):
    tutor_id = _make_user("t3@x.com", "10003", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10003", ais.STUDENT_NAME: "単独太郎", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    a = _get_assignment(tutor_id, "単独太郎")
    assert a is not None and a.parent_id is None


# --- 更新（保護者の付け替え） ---

def test_import_updates_parent_link(client):
    tutor_id = _make_user("t4@x.com", "10004", role="tutor")
    parent_a = _make_user("pa@x.com", "20004", role="parent")
    parent_b = _make_user("pb@x.com", "20005", role="parent")
    _make_assignment(tutor_id, "花子", parent_a)
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10004", ais.STUDENT_NAME: "花子", ais.PARENT_NO: "20005"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 1
    a = _get_assignment(tutor_id, "花子")
    assert a.parent_id == parent_b


def test_import_blank_parent_clears_link_on_update(client):
    tutor_id = _make_user("t5@x.com", "10005", role="tutor")
    parent_id = _make_user("p5@x.com", "20006", role="parent")
    _make_assignment(tutor_id, "太郎", parent_id)
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10005", ais.STUDENT_NAME: "太郎", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 1
    a = _get_assignment(tutor_id, "太郎")
    assert a.parent_id is None


# --- バリデーション ---

def test_import_unknown_tutor_is_error(client):
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "99999", ais.STUDENT_NAME: "誰か", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 400
    assert "講師" in str(res.json()["detail"])


def test_import_unknown_parent_is_error(client):
    _make_user("t6@x.com", "10006", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10006", ais.STUDENT_NAME: "生徒", ais.PARENT_NO: "29999"},
    ])
    assert res.status_code == 400
    assert "保護者" in str(res.json()["detail"])


def test_import_parent_no_pointing_to_non_parent_is_error(client):
    _make_user("t7@x.com", "10007", role="tutor")
    # 10008 は講師であって保護者ではない → 保護者Noとしては不正
    _make_user("t8@x.com", "10008", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10007", ais.STUDENT_NAME: "生徒", ais.PARENT_NO: "10008"},
    ])
    assert res.status_code == 400


def test_import_missing_student_is_error(client):
    _make_user("t9@x.com", "10009", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10009", ais.STUDENT_NAME: "", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 400


def test_import_csv_internal_duplicate_is_error(client):
    _make_user("t10@x.com", "10011", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10011", ais.STUDENT_NAME: "同一", ais.PARENT_NO: ""},
        {ais.TUTOR_NO: "10011", ais.STUDENT_NAME: "同一", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 400
    assert "重複" in str(res.json()["detail"])


def test_import_all_or_nothing(client):
    tutor_id = _make_user("t11@x.com", "10012", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "10012", ais.STUDENT_NAME: "有効生徒", ais.PARENT_NO: ""},  # 有効
        {ais.TUTOR_NO: "99999", ais.STUDENT_NAME: "無効生徒", ais.PARENT_NO: ""},  # 講師不在
    ])
    assert res.status_code == 400
    assert _get_assignment(tutor_id, "有効生徒") is None  # 有効な行も作成されない


# --- スキップ・テンプレート・権限 ---

def test_import_skips_comment_and_blank_rows(client):
    tutor_id = _make_user("t12@x.com", "10013", role="tutor")
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "#記入例", ais.STUDENT_NAME: "例", ais.PARENT_NO: ""},
        {},
        {ais.TUTOR_NO: "10013", ais.STUDENT_NAME: "実生徒", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    assert _get_assignment(tutor_id, "実生徒") is not None


def test_import_no_target_rows_is_error(client):
    res = _upload(client, "master@example.com", [
        {ais.TUTOR_NO: "#コメントのみ", ais.STUDENT_NAME: "x", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 400
    assert "取り込み対象の行がありません" in str(res.json()["detail"])


def test_import_header_mismatch_is_error(client):
    bad = b"\xef\xbb\xbf" + "講師,生徒\n10001,太郎\n".encode("utf-8")
    res = _upload(client, "master@example.com", bad)
    assert res.status_code == 400
    assert "見出し" in str(res.json()["detail"])


def test_import_forbidden_for_parent(client):
    res = _upload(client, "parent@example.com", [
        {ais.TUTOR_NO: "10001", ais.STUDENT_NAME: "x", ais.PARENT_NO: ""},
    ])
    assert res.status_code == 403
