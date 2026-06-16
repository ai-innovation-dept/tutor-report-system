"""初回ログイン時のパスワード変更強制（must_change_password）の統合テスト（既存システム=legacy）。

新システム(new_backend)と同じ仕様:
- must_change_password=True のユーザーはログイン時に /change-password へ誘導される。
- 強制変更時は現在のパスワード照合不要。任意変更時は必須。変更後フラグを解除する。
- フラグが立ったまま他ページへアクセスすると /change-password へリダイレクトされる。
"""
from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.database import SessionLocal
from app.models import User
from tests.conftest import token


def _make_user(email, role="tutor", must_change=True, password="Passw0rd!", allowed=("legacy",)):
    db = SessionLocal()
    try:
        u = User(
            email=email,
            role=role,
            roles=[role],
            display_name="氏名",
            password_hash=hash_password(password),
            user_no="10010",
            tutor_no="10010" if role == "tutor" else None,
            is_active=True,
            must_change_password=must_change,
            allowed_systems=list(allowed),
        )
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


def _login(client, email, password="Passw0rd!"):
    return client.post("/api/auth/login", data={"username": email, "password": password})


# --- ログイン時の誘導 ---

def test_login_forced_user_redirects_to_change_password(client):
    _make_user("forced@x.com", role="tutor", must_change=True)
    res = _login(client, "forced@x.com")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["must_change_password"] is True
    assert body["redirect_url"] == "/change-password"


def test_login_normal_user_not_forced(client):
    res = _login(client, "master@example.com")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["must_change_password"] is False
    assert body["redirect_url"] != "/change-password"


# --- 変更API（強制） ---

def test_change_password_forced_clears_flag(client):
    _make_user("forced2@x.com", role="tutor", must_change=True)
    res = client.post(
        "/api/auth/change-password",
        json={"new_password": "NewPass123"},
        headers={"Authorization": f"Bearer {token(client, 'forced2@x.com')}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["redirect_url"] == "/tutor/reports"
    user = _get_user("forced2@x.com")
    assert user.must_change_password is False
    assert verify_password("NewPass123", user.password_hash)


def test_change_password_rejects_short(client):
    _make_user("short@x.com", role="tutor", must_change=True)
    res = client.post(
        "/api/auth/change-password",
        json={"new_password": "short"},
        headers={"Authorization": f"Bearer {token(client, 'short@x.com')}"},
    )
    assert res.status_code == 422


def test_change_password_rejects_initial_password(client):
    _make_user("init@x.com", role="tutor", must_change=True)
    res = client.post(
        "/api/auth/change-password",
        json={"new_password": "Passw0rd!"},
        headers={"Authorization": f"Bearer {token(client, 'init@x.com')}"},
    )
    assert res.status_code == 422
    assert _get_user("init@x.com").must_change_password is True  # 変更されない


# --- 変更API（任意・現在パスワード必須） ---

def test_change_password_optional_requires_current(client):
    # master@example.com は通常ユーザー（must_change_password=False）
    headers = {"Authorization": f"Bearer {token(client, 'master@example.com')}"}
    # current_password 無し → 400
    res = client.post("/api/auth/change-password", json={"new_password": "NewPass123"}, headers=headers)
    assert res.status_code == 400
    # current_password 誤り → 400
    res2 = client.post("/api/auth/change-password", json={"new_password": "NewPass123", "current_password": "wrong"}, headers=headers)
    assert res2.status_code == 400
    # 正しい current_password → 200
    res3 = client.post("/api/auth/change-password", json={"new_password": "NewPass123", "current_password": "Passw0rd!"}, headers=headers)
    assert res3.status_code == 200, res3.text
    assert verify_password("NewPass123", _get_user("master@example.com").password_hash)


# --- ページガード ---

def test_page_guard_redirects_forced_user(client):
    _make_user("guard@x.com", role="tutor", must_change=True)
    _login(client, "guard@x.com")  # クッキーをクライアントに保存
    res = client.get("/tutor/reports", follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "/change-password"


def test_change_password_page_renders_for_forced_user(client):
    _make_user("page@x.com", role="tutor", must_change=True)
    _login(client, "page@x.com")
    res = client.get("/change-password", follow_redirects=False)
    assert res.status_code == 200
    assert "新しいパスワード" in res.text


def test_page_accessible_after_password_changed(client):
    _make_user("after@x.com", role="tutor", must_change=True)
    _login(client, "after@x.com")  # cookie保存
    changed = client.post(
        "/api/auth/change-password",
        json={"new_password": "NewPass123"},
        headers={"Authorization": f"Bearer {token(client, 'after@x.com')}"},
    )
    assert changed.status_code == 200, changed.text
    res = client.get("/tutor/reports", follow_redirects=False)
    assert res.status_code == 200  # もう /change-password へ飛ばされない
