"""SMTP送信のTLS/認証切替の単体テスト（既存システム）。

メールは送信キュー(mail_outbox)へ投函され、ドレイナが同期 smtplib で実送信する。
本番の外部SMTP（認証＋TLS）と開発のMailHog（認証/TLSなし）を、.env の設定だけで
切り替えられることを、smtplib をフェイクに差し替えて確認する（実送信はしない）。
"""
import pytest

from app.config import settings
from app.services import mailer

_created: list = []


class _FakeSMTP:
    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.calls: list = []
        self.is_ssl = False
        _created.append(self)

    def starttls(self):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"], message["From"]))

    def quit(self):
        self.calls.append("quit")


class _FakeSMTPSSL(_FakeSMTP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_ssl = True


@pytest.fixture(autouse=True)
def _smtp_env(monkeypatch):
    _created.clear()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_from", "from@example.com")
    monkeypatch.setattr(mailer.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", _FakeSMTPSSL)


def test_none_means_plain_no_auth(monkeypatch):
    monkeypatch.setattr(settings, "smtp_tls", "none")
    monkeypatch.setattr(settings, "smtp_username", "")
    mailer._send_via_smtp("to@example.com", "件名", "本文")
    inst = _created[-1]
    assert inst.is_ssl is False
    assert inst.host == "smtp.example" and inst.port == 587
    assert "starttls" not in inst.calls
    assert not any(isinstance(c, tuple) and c[0] == "login" for c in inst.calls)
    assert ("send", "to@example.com", "from@example.com") in inst.calls


def test_starttls_with_auth(monkeypatch):
    monkeypatch.setattr(settings, "smtp_tls", "starttls")
    monkeypatch.setattr(settings, "smtp_username", "user")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    mailer._send_via_smtp("to@example.com", "件名", "本文")
    inst = _created[-1]
    assert inst.is_ssl is False
    assert "starttls" in inst.calls
    assert ("login", "user", "secret") in inst.calls


def test_ssl_mode_implicit_tls(monkeypatch):
    monkeypatch.setattr(settings, "smtp_tls", "ssl")
    monkeypatch.setattr(settings, "smtp_username", "")
    mailer._send_via_smtp("to@example.com", "件名", "本文")
    inst = _created[-1]
    assert inst.is_ssl is True  # SMTP_SSL（暗黙TLS）を使う
    assert "starttls" not in inst.calls
