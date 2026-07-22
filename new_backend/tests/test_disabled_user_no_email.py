"""改修依頼 202607221522 ①(EMPS): 無効化ユーザーにはメールを送らない。

EMPS はワークフロー通知／リマインドの宛先解決が既に is_active・deleted_at を除外している
（notification_service._send_email / _resolve_notification_recipients / _staff_users）。
残っていた穴は forgot-password（無効化ユーザーにもリセットメールを送っていた）で、ここを塞いだ
ことを検証する。実メールは送らない（MAIL_BACKEND=console・送信キュー行の有無で判定）。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import User
from app.models.work import WorkMailOutbox
from tests.conftest import TestSession

EMAIL = "disabled@na.example.com"


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


def _add_user(db, email, role, *, is_active=True):
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=["new"],
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    return user


def _outbox_count(db, email: str) -> int:
    return db.query(WorkMailOutbox).filter(WorkMailOutbox.to_email == email).count()


def test_forgot_password_skips_disabled_user(db, client):
    """無効化ユーザーにはリセットメールを送らない（応答文言は有効時と同一で存在を明かさない）。"""
    _add_user(db, EMAIL, "office", is_active=False)
    res = client.post("/api/auth/forgot-password", json={"email": EMAIL})
    assert res.status_code == 200, res.text
    assert _outbox_count(db, EMAIL) == 0


def test_forgot_password_active_user_still_sends(db, client):
    """反証: 有効ユーザーには従来どおりリセットメールを投函する。"""
    _add_user(db, EMAIL, "office", is_active=True)
    res = client.post("/api/auth/forgot-password", json={"email": EMAIL})
    assert res.status_code == 200, res.text
    assert _outbox_count(db, EMAIL) >= 1
