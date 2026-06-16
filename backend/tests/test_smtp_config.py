"""SMTP接続パラメータ（認証/TLS）の組み立てロジックの単体テスト（既存システム）。

本番の外部SMTPサービス（認証＋TLS）と開発のMailHog（認証/TLSなし）を、
同じ送信コードで .env の設定だけ切り替えて扱えることを確認する。
"""
from app.config import settings
from app.services.notification_service import _smtp_send_kwargs


def test_none_means_plain_no_auth(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "mailhog")
    monkeypatch.setattr(settings, "smtp_port", 1025)
    monkeypatch.setattr(settings, "smtp_tls", "none")
    monkeypatch.setattr(settings, "smtp_username", "")
    kwargs = _smtp_send_kwargs()
    assert kwargs["hostname"] == "mailhog"
    assert kwargs["port"] == 1025
    assert kwargs["use_tls"] is False
    assert kwargs["start_tls"] is False
    assert "username" not in kwargs and "password" not in kwargs


def test_starttls_with_auth(monkeypatch):
    monkeypatch.setattr(settings, "smtp_tls", "starttls")
    monkeypatch.setattr(settings, "smtp_username", "user")
    monkeypatch.setattr(settings, "smtp_password", "secret")
    kwargs = _smtp_send_kwargs()
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False
    assert kwargs["username"] == "user"
    assert kwargs["password"] == "secret"


def test_ssl_mode_implicit_tls(monkeypatch):
    monkeypatch.setattr(settings, "smtp_tls", "ssl")
    monkeypatch.setattr(settings, "smtp_username", "")
    kwargs = _smtp_send_kwargs()
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False
    assert "username" not in kwargs
