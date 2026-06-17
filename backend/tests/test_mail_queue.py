"""メール送信キュー（アウトボックス）とドレイナの単体テスト（既存システム）。

要点:
- enqueue_mail / EmailChannel.send は mail_outbox に pending 行を作るだけ（即時送信しない）。
- drain_outbox は MAIL_BACKEND=smtp のときだけ動き、1通ずつ順次送信して sent にする。
  console（既定・テスト）では何もしない＝実送信ゼロ。
- 送信失敗時は試行回数を加算し、上限で failed として打ち切る。

※実 SMTP は一切呼ばない（_deliver / smtplib をフェイクに差し替える）。
"""
import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import MailOutbox
from app.services import mailer
from app.services.notification_service import EmailChannel


def _fresh_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _insert(engine, to_email):
    with Session(engine) as session:
        session.add(MailOutbox(to_email=to_email, subject="S", body="B", status="pending"))
        session.commit()


def _rows(engine):
    with Session(engine) as session:
        return list(session.scalars(select(MailOutbox).order_by(MailOutbox.created_at)))


def test_enqueue_mail_creates_pending_row(db):
    mailer.enqueue_mail("a@example.com", "件名", "本文")
    row = db.query(MailOutbox).filter(MailOutbox.to_email == "a@example.com").one()
    assert row.status == "pending"
    assert row.sent_at is None


def test_email_channel_send_enqueues(db):
    # EmailChannel.send は即時送信せず投函する（全送信経路がこれを通る）
    asyncio.run(EmailChannel().send("z@example.com", "件名", "本文"))
    row = db.query(MailOutbox).filter(MailOutbox.to_email == "z@example.com").one()
    assert row.status == "pending"


def test_drain_console_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "console")
    engine = _fresh_engine()
    _insert(engine, "a@example.com")
    assert mailer.drain_outbox(engine_override=engine) == 0
    assert _rows(engine)[0].status == "pending"


def test_drain_smtp_sends_in_order(monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "smtp")
    monkeypatch.setattr(settings, "mail_send_interval_seconds", 0)  # テストでは待機しない
    delivered: list = []
    monkeypatch.setattr(mailer, "_deliver", lambda to, subj, body: delivered.append(to))

    engine = _fresh_engine()
    _insert(engine, "first@example.com")
    _insert(engine, "second@example.com")

    sent = mailer.drain_outbox(engine_override=engine)
    assert sent == 2  # 1通ずつ順次処理して2通とも送信
    assert set(delivered) == {"first@example.com", "second@example.com"}
    assert all(r.status == "sent" and r.sent_at is not None for r in _rows(engine))


def test_drain_failure_retries_then_fails(monkeypatch):
    monkeypatch.setattr(settings, "mail_backend", "smtp")
    monkeypatch.setattr(settings, "mail_send_interval_seconds", 0)
    monkeypatch.setattr(settings, "mail_max_attempts", 2)

    def boom(*args):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "_deliver", boom)

    engine = _fresh_engine()
    _insert(engine, "x@example.com")

    assert mailer.drain_outbox(engine_override=engine) == 0
    row = _rows(engine)[0]
    assert row.status == "pending" and row.attempts == 1  # 1回目失敗 → 再試行待ち

    assert mailer.drain_outbox(engine_override=engine) == 0
    row = _rows(engine)[0]
    assert row.status == "failed" and row.attempts == 2  # 上限到達で打ち切り
