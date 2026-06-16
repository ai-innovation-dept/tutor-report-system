"""ユーザー管理のCSVエクスポート/インポート（フェーズ①：既存ユーザーの更新のみ）の統合テスト。

照合キー=No(user_no)。一致した既存ユーザーの「メールアドレス・氏名」のみ上書き更新する。
No空欄(=新規作成)は次フェーズ②で対応するため、現フェーズではエラーになることを確認する。
"""
import csv
import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import User
from app.services import user_import_service as uis
from tests.conftest import TestSession


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def master_user():
    db = TestSession()
    u = User(
        email="master@new.example.com",
        role="admin_master",
        roles=["admin_master"],
        display_name="管理者",
        password_hash=hash_password("Passw0rd!"),
        user_no="50001",
        allowed_systems=["legacy", "new"],
    )
    db.add(u)
    db.commit()
    db.close()
    return u


def _auth(client, email="master@new.example.com", password="Passw0rd!"):
    res = client.post("/api/auth/login", json={"username": email, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _make_user(email, user_no, name="氏名", roles=("office",), is_active=True, allowed=("new",), deleted=False):
    db = TestSession()
    u = User(
        email=email,
        role=roles[0],
        roles=list(roles),
        display_name=name,
        password_hash=hash_password("Passw0rd!"),
        user_no=user_no,
        is_active=is_active,
        allowed_systems=list(allowed),
    )
    if deleted:
        from datetime import datetime, timezone
        u.deleted_at = datetime.now(timezone.utc)
    db.add(u)
    db.commit()
    uid = u.id
    db.close()
    return uid


def _csv_bytes(rows: list[dict]) -> bytes:
    """テンプレート見出しを備えたCSV(UTF-8 BOM)を生成する。"""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=uis.headers())
    writer.writeheader()
    for row in rows:
        writer.writerow({h: row.get(h, "") for h in uis.headers()})
    return buf.getvalue().encode("utf-8-sig")


def _import(client, rows, headers):
    return client.post(
        "/api/w/users/import",
        files={"file": ("users.csv", _csv_bytes(rows), "text/csv")},
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# エクスポート
# --------------------------------------------------------------------------- #
class TestExport:
    def test_export_has_header(self, client, master_user):
        res = client.get("/api/w/users/export", headers=_auth(client))
        assert res.status_code == 200, res.text
        assert res.headers["content-type"].startswith("text/csv")
        text = res.content.decode("utf-8-sig")
        first = text.splitlines()[0]
        assert first.split(",")[:3] == [uis.NO, uis.EMAIL, uis.NAME]

    def test_export_includes_new_user(self, client, master_user):
        _make_user("staff@new.example.com", "50002", name="事務太郎", roles=("office",))
        res = client.get("/api/w/users/export", headers=_auth(client))
        text = res.content.decode("utf-8-sig")
        assert "50002" in text and "staff@new.example.com" in text and "事務太郎" in text

    def test_export_excludes_legacy_only_and_deleted(self, client, master_user):
        _make_user("legacy@x.example.com", "60001", allowed=("legacy",))  # newなし→除外
        _make_user("gone@x.example.com", "60002", deleted=True)            # 削除→除外
        res = client.get("/api/w/users/export", headers=_auth(client))
        text = res.content.decode("utf-8-sig")
        assert "legacy@x.example.com" not in text
        assert "gone@x.example.com" not in text

    def test_export_requires_auth(self, client, master_user):
        assert client.get("/api/w/users/export").status_code in (401, 403)


# --------------------------------------------------------------------------- #
# インポート（更新）
# --------------------------------------------------------------------------- #
class TestImportUpdate:
    def test_updates_email_and_name(self, client, master_user):
        uid = _make_user("old@new.example.com", "50010", name="旧名")
        res = _import(client, [{uis.NO: "50010", uis.EMAIL: "new@new.example.com", uis.NAME: "新名"}], _auth(client))
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 1, "created": 0, "revived": 0, "updated": 1}
        db = TestSession()
        u = db.get(User, uid)
        assert u.email == "new@new.example.com"
        assert u.display_name == "新名"
        db.close()

    def test_reference_columns_are_ignored(self, client, master_user):
        """ロール(参考)・状態(参考)をCSVで変えても適用されない（メール・氏名のみ更新）。"""
        uid = _make_user("ref@new.example.com", "50011", name="名前", roles=("office",), is_active=True)
        res = _import(client, [{
            uis.NO: "50011", uis.EMAIL: "ref@new.example.com", uis.NAME: "更新名",
            uis.ROLE: "admin_master", uis.STATUS_REF: "無効", uis.SKIP_APPROVAL_REF: "有",
        }], _auth(client))
        assert res.status_code == 200, res.text
        db = TestSession()
        u = db.get(User, uid)
        assert u.display_name == "更新名"      # 氏名は更新される
        assert u.roles == ["office"]            # ロールは不変
        assert u.role == "office"
        assert u.is_active is True              # 状態は不変
        assert u.skip_parent_approval is False  # 学校承認スキップは不変
        db.close()

    def test_unknown_no_is_error(self, client, master_user):
        res = _import(client, [{uis.NO: "99999", uis.EMAIL: "x@new.example.com", uis.NAME: "誰か"}], _auth(client))
        assert res.status_code == 400
        assert "99999" in " ".join(res.json()["detail"]["errors"])

    def test_duplicate_email_with_existing_user(self, client, master_user):
        """既に他ユーザーが使っているメールへ変更しようとするとエラー。"""
        _make_user("taken@new.example.com", "50020", name="A")
        _make_user("target@new.example.com", "50021", name="B")
        res = _import(client, [{uis.NO: "50021", uis.EMAIL: "taken@new.example.com", uis.NAME: "B"}], _auth(client))
        assert res.status_code == 400
        assert "taken@new.example.com" in " ".join(res.json()["detail"]["errors"])

    def test_duplicate_email_within_csv(self, client, master_user):
        _make_user("a@new.example.com", "50030", name="A")
        _make_user("b@new.example.com", "50031", name="B")
        res = _import(client, [
            {uis.NO: "50030", uis.EMAIL: "same@new.example.com", uis.NAME: "A"},
            {uis.NO: "50031", uis.EMAIL: "same@new.example.com", uis.NAME: "B"},
        ], _auth(client))
        assert res.status_code == 400

    def test_all_or_nothing(self, client, master_user):
        """1件でもエラーがあれば全件取り込まない。"""
        uid = _make_user("keep@new.example.com", "50040", name="元名")
        res = _import(client, [
            {uis.NO: "50040", uis.EMAIL: "ok@new.example.com", uis.NAME: "新名"},
            {uis.NO: "99999", uis.EMAIL: "bad@new.example.com", uis.NAME: "存在しない"},
        ], _auth(client))
        assert res.status_code == 400
        db = TestSession()
        u = db.get(User, uid)
        assert u.email == "keep@new.example.com"  # 1件目も適用されていない
        assert u.display_name == "元名"
        db.close()

    def test_skips_comment_and_blank_rows(self, client, master_user):
        uid = _make_user("c@new.example.com", "50050", name="名前")
        res = _import(client, [
            {uis.NO: "#記入例", uis.EMAIL: "ignore@x", uis.NAME: "例"},     # #始まり→スキップ
            {},                                                              # 全空白→スキップ
            {uis.NO: "50050", uis.EMAIL: "c2@new.example.com", uis.NAME: "新名"},
        ], _auth(client))
        assert res.status_code == 200, res.text
        assert res.json()["updated"] == 1
        db = TestSession()
        assert db.get(User, uid).email == "c2@new.example.com"
        db.close()

    def test_invalid_email_format(self, client, master_user):
        _make_user("v@new.example.com", "50060", name="名前")
        res = _import(client, [{uis.NO: "50060", uis.EMAIL: "not-an-email", uis.NAME: "名前"}], _auth(client))
        assert res.status_code == 400

    def test_header_mismatch(self, client, master_user):
        bad = ("No,氏名\n50001,テスト\n").encode("utf-8-sig")  # メールアドレス列が無い
        res = client.post(
            "/api/w/users/import",
            files={"file": ("u.csv", bad, "text/csv")},
            headers=_auth(client),
        )
        assert res.status_code == 400

    def test_import_requires_auth(self, client, master_user):
        res = client.post("/api/w/users/import", files={"file": ("u.csv", _csv_bytes([]), "text/csv")})
        assert res.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# インポート（新規作成：No空欄）
# --------------------------------------------------------------------------- #
class TestImportCreate:
    def test_creates_new_user_with_initial_password(self, client, master_user):
        from app.core.security import verify_password
        res = _import(client, [{uis.NO: "", uis.ROLE: "office", uis.EMAIL: "newoffice@new.example.com", uis.NAME: "新人事務"}], _auth(client))
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 1, "created": 1, "revived": 0, "updated": 0}
        db = TestSession()
        u = db.scalar(select(User).where(User.email == "newoffice@new.example.com"))
        assert u is not None
        assert u.display_name == "新人事務"
        assert u.roles == ["office"]
        assert u.must_change_password is True            # 初回ログイン時の変更が必須
        assert verify_password("Passw0rd!", u.password_hash)  # 初期パスワード
        assert "new" in (u.allowed_systems or [])
        assert u.user_no and 50001 <= int(u.user_no) <= 59999  # 事務帯で自動採番
        db.close()

    def test_new_row_requires_role(self, client, master_user):
        res = _import(client, [{uis.NO: "", uis.ROLE: "", uis.EMAIL: "x@new.example.com", uis.NAME: "新規"}], _auth(client))
        assert res.status_code == 400
        assert "ロール" in " ".join(res.json()["detail"]["errors"])

    def test_new_row_invalid_role(self, client, master_user):
        res = _import(client, [{uis.NO: "", uis.ROLE: "wizard", uis.EMAIL: "x@new.example.com", uis.NAME: "新規"}], _auth(client))
        assert res.status_code == 400

    def test_admin_chief_requires_chief_requester(self, client, master_user):
        """admin_master(=master_user)は admin_chief を作成できない。"""
        res = _import(client, [{uis.NO: "", uis.ROLE: "admin_chief", uis.EMAIL: "chief@new.example.com", uis.NAME: "責任者"}], _auth(client))
        assert res.status_code == 400
        assert "admin_chief" in " ".join(res.json()["detail"]["errors"])

    def test_create_duplicate_email_existing(self, client, master_user):
        _make_user("dup@new.example.com", "50070", name="既存")
        res = _import(client, [{uis.NO: "", uis.ROLE: "office", uis.EMAIL: "dup@new.example.com", uis.NAME: "新規"}], _auth(client))
        assert res.status_code == 400
        assert "dup@new.example.com" in " ".join(res.json()["detail"]["errors"])

    def test_create_and_update_in_one_file(self, client, master_user):
        uid = _make_user("upd@new.example.com", "50080", name="旧名")
        res = _import(client, [
            {uis.NO: "50080", uis.EMAIL: "upd2@new.example.com", uis.NAME: "新名"},          # 更新
            {uis.NO: "", uis.ROLE: "sales", uis.EMAIL: "brandnew@new.example.com", uis.NAME: "新営業"},  # 新規
        ], _auth(client))
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 2, "created": 1, "revived": 0, "updated": 1}
        db = TestSession()
        assert db.get(User, uid).email == "upd2@new.example.com"
        assert db.scalar(select(User).where(User.email == "brandnew@new.example.com")) is not None
        db.close()

    def test_email_is_lowercased_on_create(self, client, master_user):
        res = _import(client, [{uis.NO: "", uis.ROLE: "office", uis.EMAIL: "Mixed@New.Example.com", uis.NAME: "大文字"}], _auth(client))
        assert res.status_code == 200, res.text
        db = TestSession()
        assert db.scalar(select(User).where(User.email == "mixed@new.example.com")) is not None
        db.close()


# --------------------------------------------------------------------------- #
# ① 削除済みメールの再利用（同一アカウントの復活）
# --------------------------------------------------------------------------- #
class TestDeletedEmailReuse:
    def test_new_row_with_deleted_email_revives_account(self, client, master_user):
        """削除済みユーザーのメールを新規作成行(No空欄)で取り込むと同一アカウントを復活させる。"""
        from app.core.security import verify_password
        uid = _make_user("gone@new.example.com", "50005", name="旧人", roles=("office",), deleted=True)
        res = _import(client, [{
            uis.NO: "", uis.ROLE: "sales", uis.EMAIL: "gone@new.example.com", uis.NAME: "復活太郎",
        }], _auth(client))
        assert res.status_code == 200, res.text
        assert res.json() == {"imported": 1, "created": 0, "revived": 1, "updated": 0}
        db = TestSession()
        # 同一アカウント(id不変)が復活し、ロール・氏名はCSVの内容に更新される。
        u = db.get(User, uid)
        assert u.deleted_at is None
        assert u.is_active is True
        assert u.roles == ["sales"] and u.role == "sales"
        assert u.display_name == "復活太郎"
        assert u.must_change_password is True
        assert verify_password("Passw0rd!", u.password_hash)
        assert "new" in (u.allowed_systems or [])
        assert u.user_no and 50001 <= int(u.user_no) <= 59999
        # メールアドレスの行は重複せず1件のまま（別行を作らない）。
        holders = db.scalars(select(User).where(User.email == "gone@new.example.com")).all()
        assert len(holders) == 1 and holders[0].id == uid
        db.close()

    def test_new_row_with_active_email_still_errors(self, client, master_user):
        """有効ユーザーのメールは新規作成行でも再利用できない（衝突エラー）。"""
        _make_user("live@new.example.com", "50005", name="現役", roles=("office",))
        res = _import(client, [{
            uis.NO: "", uis.ROLE: "sales", uis.EMAIL: "live@new.example.com", uis.NAME: "別人",
        }], _auth(client))
        assert res.status_code == 400
        assert "live@new.example.com" in " ".join(res.json()["detail"]["errors"])

    def test_update_row_to_deleted_email_is_guided_error(self, client, master_user):
        """更新行が削除済みメールを要求した場合は、新規作成行での再利用を案内するエラー。"""
        _make_user("dgone@new.example.com", "50006", name="削除済み", roles=("office",), deleted=True)
        _make_user("u@new.example.com", "50007", name="現役U", roles=("office",))
        res = _import(client, [{uis.NO: "50007", uis.EMAIL: "dgone@new.example.com", uis.NAME: "現役U"}], _auth(client))
        assert res.status_code == 400
        joined = " ".join(res.json()["detail"]["errors"])
        assert "削除済み" in joined and "新規作成行" in joined


# --------------------------------------------------------------------------- #
# ② No自動採番：未使用の最小番号を埋める／削除済みのNoを再利用する
# --------------------------------------------------------------------------- #
class TestNumberingGapFill:
    def test_new_number_fills_smallest_gap(self, client, master_user):
        """歯抜けの若いNoを優先採番する（max+1ではない）。"""
        # master_user=50001。50002・50004を使用済みにすると最小の空きは50003。
        _make_user("a@new.example.com", "50002", roles=("office",))
        _make_user("b@new.example.com", "50004", roles=("office",))
        res = _import(client, [{uis.NO: "", uis.ROLE: "office", uis.EMAIL: "gap@new.example.com", uis.NAME: "穴埋め"}], _auth(client))
        assert res.status_code == 200, res.text
        db = TestSession()
        u = db.scalar(select(User).where(User.email == "gap@new.example.com"))
        assert u.user_no == "50003"
        db.close()

    def test_new_number_reuses_deleted_no(self, client, master_user):
        """削除済みユーザーのNoは解放され、次の新規作成で再利用される。"""
        # master_user=50001。50002は削除済み→解放。最小の空きは50002。
        _make_user("del@new.example.com", "50002", roles=("office",), deleted=True)
        res = _import(client, [{uis.NO: "", uis.ROLE: "office", uis.EMAIL: "fresh@new.example.com", uis.NAME: "新人"}], _auth(client))
        assert res.status_code == 200, res.text
        db = TestSession()
        u = db.scalar(select(User).where(User.email == "fresh@new.example.com"))
        assert u.user_no == "50002"
        db.close()

    def test_consecutive_creates_get_distinct_numbers(self, client, master_user):
        """同一CSV内の複数新規作成が連番で別々のNoになる（採番の二重付与なし）。"""
        res = _import(client, [
            {uis.NO: "", uis.ROLE: "office", uis.EMAIL: "n1@new.example.com", uis.NAME: "一"},
            {uis.NO: "", uis.ROLE: "sales", uis.EMAIL: "n2@new.example.com", uis.NAME: "二"},
        ], _auth(client))
        assert res.status_code == 200, res.text
        assert res.json()["created"] == 2
        db = TestSession()
        nos = {
            db.scalar(select(User).where(User.email == "n1@new.example.com")).user_no,
            db.scalar(select(User).where(User.email == "n2@new.example.com")).user_no,
        }
        assert nos == {"50002", "50003"}  # master_user=50001 の次から連番
        db.close()
