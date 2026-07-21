"""ユーザー管理のCSVエクスポート/インポートの統合テスト（既存システム=legacy）。

照合キー=No(user_no)。No一致の既存ユーザーはメール・氏名のみ更新、No空欄は新規作成
（ロール必須・user_no自動採番・初期パスワードPassw0rd!・must_change_password=True）。
削除済みユーザーのメールは解放済みのため、同じアドレスは新規アカウントとして作成される（202607210807 ②）。1件でもエラーなら全件中止。
"""
import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.models import User
from app.services import user_import_service as uis
from tests.conftest import token


def _headers(client, email):
    return {"Authorization": f"Bearer {token(client, email)}"}


def _make_user(email, user_no, name="氏名", roles=("admin_receiver",), allowed=("legacy",), deleted=False):
    db = SessionLocal()
    try:
        u = User(
            email=email,
            role=roles[0],
            roles=list(roles),
            display_name=name,
            password_hash=hash_password("Passw0rd!"),
            user_no=user_no,
            is_active=True,
            allowed_systems=list(allowed),
        )
        if deleted:
            u.deleted_at = datetime.now(timezone.utc)
        db.add(u)
        db.commit()
        return u.id
    finally:
        db.close()


def _get_user(email):
    db = SessionLocal()
    try:
        return db.scalar(select(User).where(User.email == email.lower()))
    finally:
        db.close()


def _csv_bytes(rows):
    """rows: list[dict]（キーはuis.headers()のいずれか。未指定列は空欄）。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=uis.headers())
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in uis.headers()})
    return buf.getvalue().encode("utf-8-sig")


def _upload(client, email, rows_or_bytes):
    data = rows_or_bytes if isinstance(rows_or_bytes, (bytes, bytearray)) else _csv_bytes(rows_or_bytes)
    return client.post(
        "/api/users/import",
        files={"file": ("users.csv", data, "text/csv")},
        headers=_headers(client, email),
    )


# --- エクスポート ---

def test_export_returns_csv_with_bom_and_headers(client):
    res = client.get("/api/users/export", headers=_headers(client, "master@example.com"))
    assert res.status_code == 200, res.text
    assert res.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    text = res.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    assert reader.fieldnames == uis.headers()
    emails = [row[uis.EMAIL] for row in reader]
    assert "master@example.com" in emails  # legacy所属ユーザーが含まれる


def test_export_forbidden_for_tutor(client):
    res = client.get("/api/users/export", headers=_headers(client, "tutor@example.com"))
    assert res.status_code == 403


# --- 更新（No一致） ---

def test_import_update_changes_email_and_name_only(client):
    _make_user("old@x.com", "30005", name="旧名", roles=("admin_receiver",))
    res = _upload(client, "master@example.com", [
        {uis.NO: "30005", uis.EMAIL: "new@x.com", uis.NAME: "新名", uis.ROLE: "tutor"},  # ロールは無視される
    ])
    assert res.status_code == 200, res.text
    assert res.json()["updated"] == 1
    user = _get_user("new@x.com")
    assert user is not None
    assert user.display_name == "新名"
    assert user.role == "admin_receiver"  # ロールは変わらない
    assert user.user_no == "30005"


def test_import_update_unknown_no_is_error(client):
    res = _upload(client, "master@example.com", [
        {uis.NO: "99999", uis.EMAIL: "a@x.com", uis.NAME: "氏名"},
    ])
    assert res.status_code == 400
    assert "見つかりません" in str(res.json()["detail"])


# --- 新規作成（No空欄） ---

def test_import_create_new_tutor(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "t1@x.com", uis.NAME: "新講師", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    user = _get_user("t1@x.com")
    assert user is not None
    assert user.roles == ["tutor"]
    assert user.must_change_password is True
    assert user.allowed_systems == ["legacy"]
    assert user.user_no and 10001 <= int(user.user_no) <= 19999
    assert user.tutor_no == user.user_no
    assert verify_password("Passw0rd!", user.password_hash)


def test_import_create_parent_account_only(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "p2@x.com", uis.NAME: "保護者花子", uis.ROLE: "parent"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    user = _get_user("p2@x.com")
    assert user is not None and user.role == "parent"
    assert user.allowed_systems == ["legacy"]


def test_import_create_admin_master_spans_both_systems(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "m2@x.com", uis.NAME: "管理者2", uis.ROLE: "admin_master"},
    ])
    assert res.status_code == 200, res.text
    user = _get_user("m2@x.com")
    assert sorted(user.allowed_systems) == ["legacy", "new"]


def test_import_consecutive_creates_get_distinct_nos(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "t2@x.com", uis.NAME: "講師A", uis.ROLE: "tutor"},
        {uis.EMAIL: "t3@x.com", uis.NAME: "講師B", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 2
    a = _get_user("t2@x.com")
    b = _get_user("t3@x.com")
    assert a.user_no != b.user_no


# --- バリデーション ---

def test_import_missing_email_is_error(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "", uis.NAME: "氏名", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 400
    assert _get_user("") is None


def test_import_invalid_role_is_error(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "bad@x.com", uis.NAME: "氏名", uis.ROLE: "superuser"},
    ])
    assert res.status_code == 400
    assert _get_user("bad@x.com") is None


def test_import_new_row_requires_role(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "norole@x.com", uis.NAME: "氏名"},
    ])
    assert res.status_code == 400


def test_import_admin_chief_requires_chief(client):
    # 管理者(master)は admin_chief を作れない
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "chief2@x.com", uis.NAME: "管責", uis.ROLE: "admin_chief"},
    ])
    assert res.status_code == 400
    assert _get_user("chief2@x.com") is None
    # 管理責任者(chief)は作れる
    _make_user("chief@x.com", "90001", name="管責", roles=("admin_chief",), allowed=("legacy", "new"))
    res2 = _upload(client, "chief@x.com", [
        {uis.EMAIL: "chief3@x.com", uis.NAME: "管責3", uis.ROLE: "admin_chief"},
    ])
    assert res2.status_code == 200, res2.text
    assert _get_user("chief3@x.com") is not None


def test_import_duplicate_email_in_csv_is_error(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "dup@x.com", uis.NAME: "A", uis.ROLE: "tutor"},
        {uis.EMAIL: "dup@x.com", uis.NAME: "B", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 400
    assert _get_user("dup@x.com") is None


def test_import_existing_email_conflict_is_error(client):
    _make_user("taken@x.com", "30006")
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "taken@x.com", uis.NAME: "新規", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 400


def test_import_all_or_nothing(client):
    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "ok@x.com", uis.NAME: "OK", uis.ROLE: "tutor"},        # 有効
        {uis.EMAIL: "", uis.NAME: "NG", uis.ROLE: "tutor"},                # 無効
    ])
    assert res.status_code == 400
    assert _get_user("ok@x.com") is None  # 有効な行も登録されない


# --- 削除済みメールの再利用（削除でメールを解放＝別アカウントとして作り直す・202607210807 ②）---

def test_import_reuses_released_email_as_new_account(client):
    """削除したユーザーのメールは解放され、CSV新規作成行で別アカウントとして登録できる。"""
    old_id = _make_user("reuse@x.com", "30007", name="旧", roles=("admin_receiver",))
    res = client.delete(f"/api/users/{old_id}", headers=_headers(client, "master@example.com"))
    assert res.status_code == 200, res.text

    res = _upload(client, "master@example.com", [
        {uis.EMAIL: "reuse@x.com", uis.NAME: "再登録太郎", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    user = _get_user("reuse@x.com")
    assert user.id != old_id  # 復活ではなく別アカウント
    assert user.deleted_at is None
    assert user.role == "tutor"
    assert user.must_change_password is True

    db = SessionLocal()
    try:
        old = db.get(User, old_id)
        assert old.deleted_at is not None      # 旧アカウントは削除済みのまま残る（履歴保持）
        assert old.email.endswith("@deleted.invalid")  # メールは解放済み
    finally:
        db.close()


# --- スキップ・テンプレート ---

def test_import_skips_comment_and_blank_rows(client):
    res = _upload(client, "master@example.com", [
        {uis.NO: "#記入例", uis.EMAIL: "example@x.com", uis.NAME: "例", uis.ROLE: "tutor"},
        {},  # 全列空白
        {uis.EMAIL: "real@x.com", uis.NAME: "実ユーザー", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 200, res.text
    assert res.json()["created"] == 1
    assert _get_user("example@x.com") is None
    assert _get_user("real@x.com") is not None


def test_import_no_target_rows_is_error(client):
    res = _upload(client, "master@example.com", [
        {uis.NO: "#コメントのみ", uis.EMAIL: "x@x.com", uis.NAME: "x", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 400
    assert "取り込み対象の行がありません" in str(res.json()["detail"])


def test_import_header_mismatch_is_error(client):
    bad = b"\xef\xbb\xbf" + "氏名,メール\nA,a@x.com\n".encode("utf-8")
    res = _upload(client, "master@example.com", bad)
    assert res.status_code == 400
    assert "見出し" in str(res.json()["detail"])


def test_import_forbidden_for_parent(client):
    res = _upload(client, "parent@example.com", [
        {uis.EMAIL: "x@x.com", uis.NAME: "x", uis.ROLE: "tutor"},
    ])
    assert res.status_code == 403
