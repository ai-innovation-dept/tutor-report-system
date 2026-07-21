"""ユーザー削除でメールアドレスを解放する挙動のテスト（改修依頼 202607210807 ②）。

削除は行を残したまま（過去の報告書・監査ログの参照整合性のため）メールアドレスだけを
不達のダミー値へ解放する。これにより同じアドレスで新規作成・コピー作成ができる。
本テストは実メールを送出しない（MAIL_BACKEND=console 既定＋送信キューが空であることを確認）。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import User
from app.models.work import WorkMailOutbox
from tests.conftest import TestSession

EMAIL = "leaver@del.example.com"


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


def _add_user(db, email, role, *, display_name=None):
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=display_name or f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=["new"],
    )
    db.add(user)
    db.commit()
    return user


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class TestDeleteReleasesEmail:
    def test_delete_releases_email_and_keeps_row(self, db, client):
        _add_user(db, "master@del.example.com", "admin_master")
        target = _add_user(db, EMAIL, "office", display_name="退職者")
        _add_user(db, "keep@del.example.com", "office", display_name="残る事務")  # 最後の1人ガード回避
        target_id = target.id

        res = client.delete(f"/api/w/users/{target_id}", headers=_auth(client, "master@del.example.com"))
        assert res.status_code == 200, res.text

        db.expire_all()
        row = db.get(User, target_id)
        assert row is not None                     # 行は残る（履歴の参照整合性）
        assert row.deleted_at is not None
        assert row.is_active is False
        assert row.email != EMAIL                  # メールは解放済み
        assert row.email.endswith("@deleted.invalid")
        assert db.scalar(select(User).where(User.email == EMAIL)) is None

    def test_copy_can_reuse_deleted_email(self, db, client):
        """削除したアドレスでコピー新規登録ができる（以前は409になっていた）。"""
        _add_user(db, "master2@del.example.com", "admin_master")
        target = _add_user(db, EMAIL, "office", display_name="退職者2")
        source = _add_user(db, "src@del.example.com", "office", display_name="コピー元事務")
        headers = _auth(client, "master2@del.example.com")
        assert client.delete(f"/api/w/users/{target.id}", headers=headers).status_code == 200

        res = client.post("/api/w/users/copy", json={
            "source_user_id": str(source.id), "display_name": "後任事務", "email": EMAIL,
        }, headers=headers)
        assert res.status_code == 201, res.text
        assert res.json()["email"] == EMAIL
        # コピーは招待メールを送らない＝送信キューは空（実メールは飛ばない）
        assert db.query(WorkMailOutbox).count() == 0

    def test_invite_can_reuse_deleted_email(self, db, client):
        """削除したアドレスで新規ユーザー登録（招待）ができる。"""
        _add_user(db, "master3@del.example.com", "admin_master")
        target = _add_user(db, EMAIL, "office", display_name="退職者3")
        _add_user(db, "keep3@del.example.com", "office", display_name="残る事務3")
        headers = _auth(client, "master3@del.example.com")
        assert client.delete(f"/api/w/users/{target.id}", headers=headers).status_code == 200

        res = client.post("/api/w/invitations", json={"role": "office", "email": EMAIL}, headers=headers)
        assert res.status_code == 201, res.text
