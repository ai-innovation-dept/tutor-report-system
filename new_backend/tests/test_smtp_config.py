"""SMTP接続パラメータ（認証/TLS）の組み立てロジックの単体テスト（新システム）。

本番の外部SMTPサービス（認証＋TLS）と開発のMailHog（認証/TLSなし）を、
同じ送信コードで .env の設定だけ切り替えて扱えることを確認する。
"""
from app.core.config import settings
from app.services.notification_service import _smtp_send_kwargs


def test_none_means_plain_no_auth(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_TLS", "none")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "")
    kwargs = _smtp_send_kwargs("mailhog", 1025)
    assert kwargs["hostname"] == "mailhog"
    assert kwargs["port"] == 1025
    assert kwargs["use_tls"] is False
    assert kwargs["start_tls"] is False
    assert "username" not in kwargs and "password" not in kwargs


def test_starttls_with_auth(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_TLS", "starttls")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "user")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "secret")
    kwargs = _smtp_send_kwargs("smtp.example", 587)
    assert kwargs["start_tls"] is True
    assert kwargs["use_tls"] is False
    assert kwargs["username"] == "user"
    assert kwargs["password"] == "secret"


def test_ssl_mode_implicit_tls(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_TLS", "ssl")
    monkeypatch.setattr(settings, "SMTP_USERNAME", "")
    kwargs = _smtp_send_kwargs("smtp.example", 465)
    assert kwargs["use_tls"] is True
    assert kwargs["start_tls"] is False
    assert "username" not in kwargs
