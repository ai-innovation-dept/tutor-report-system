"""初回ログイン時パスワード変更必須フローのテスト。

CSV一括作成ユーザー等は初期パスワード(Passw0rd!)＋must_change_password=Trueで作られ、
初回ログイン後はパスワード変更画面へ強制誘導され、変更するまで他画面を使えない。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.main import app
from app.models.shared import User
from tests.conftest import TestSession


@pytest.fixture()
def client():
    return TestClient(app)


def _make_user(email, *, role="office", must_change=False, password="Passw0rd!"):
    db = TestSession()
    u = User(
        email=email,
        role=role,
        roles=[role],
        display_name="テスト",
        password_hash=hash_password(password),
        user_no="50100",
        is_active=True,
        allowed_systems=["new"],
        must_change_password=must_change,
    )
    db.add(u)
    db.commit()
    db.close()


def _login(client, email, password="Passw0rd!"):
    return client.post("/api/auth/login", json={"username": email, "password": password})


def _bearer(res):
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class TestLoginRedirect:
    def test_must_change_user_redirected_to_change_password(self, client):
        _make_user("mc@new.example.com", must_change=True)
        res = _login(client, "mc@new.example.com")
        assert res.status_code == 200, res.text
        assert res.json()["redirect_url"] == "/change-password"

    def test_normal_user_redirected_to_dashboard(self, client):
        _make_user("ok@new.example.com", role="office", must_change=False)
        res = _login(client, "ok@new.example.com")
        assert res.json()["redirect_url"] == "/office/queue"


class TestForcedChange:
    def test_change_clears_flag_and_switches_password(self, client):
        _make_user("f@new.example.com", role="office", must_change=True)
        token_res = _login(client, "f@new.example.com")
        res = client.post("/api/auth/change-password", json={"new_password": "NewPass123"}, headers=_bearer(token_res))
        assert res.status_code == 200, res.text
        assert res.json()["redirect_url"] == "/office/queue"  # 単一ロール→ダッシュボードへ

        db = TestSession()
        u = db.scalar(select(User).where(User.email == "f@new.example.com"))
        assert u.must_change_password is False
        assert verify_password("NewPass123", u.password_hash)
        db.close()

        # 旧パスワードは不可、新パスワードで通常ログイン（変更画面へは飛ばない）
        assert _login(client, "f@new.example.com", "Passw0rd!").status_code == 401
        again = _login(client, "f@new.example.com", "NewPass123")
        assert again.status_code == 200
        assert again.json()["redirect_url"] == "/office/queue"

    def test_rejects_short_password(self, client):
        _make_user("s@new.example.com", must_change=True)
        res = client.post("/api/auth/change-password", json={"new_password": "short"}, headers=_bearer(_login(client, "s@new.example.com")))
        assert res.status_code == 422

    def test_rejects_initial_password(self, client):
        _make_user("i@new.example.com", must_change=True)
        res = client.post("/api/auth/change-password", json={"new_password": "Passw0rd!"}, headers=_bearer(_login(client, "i@new.example.com")))
        assert res.status_code == 422


class TestVoluntaryChange:
    def test_requires_current_password(self, client):
        _make_user("v@new.example.com", must_change=False)
        h = _bearer(_login(client, "v@new.example.com"))
        # 現在のパスワード未指定 → 400
        assert client.post("/api/auth/change-password", json={"new_password": "NewPass123"}, headers=h).status_code == 400
        # 誤り → 400
        assert client.post("/api/auth/change-password", json={"new_password": "NewPass123", "current_password": "wrong"}, headers=h).status_code == 400
        # 正しい → 200
        ok = client.post("/api/auth/change-password", json={"new_password": "NewPass123", "current_password": "Passw0rd!"}, headers=h)
        assert ok.status_code == 200, ok.text


class TestPageGate:
    def test_must_change_user_blocked_from_pages(self, client):
        _make_user("g@new.example.com", role="office", must_change=True)
        _login(client, "g@new.example.com")  # Cookie がクライアントjarに保存される
        res = client.get("/office/queue", follow_redirects=False)
        assert res.status_code == 302
        assert "/change-password" in res.headers["location"]

    def test_change_password_page_accessible(self, client):
        _make_user("p@new.example.com", role="office", must_change=True)
        _login(client, "p@new.example.com")
        res = client.get("/change-password", follow_redirects=False)
        assert res.status_code == 200
        assert "パスワード" in res.text

    def test_normal_user_can_reach_page(self, client):
        _make_user("n@new.example.com", role="office", must_change=False)
        _login(client, "n@new.example.com")
        res = client.get("/office/queue", follow_redirects=False)
        assert res.status_code == 200
