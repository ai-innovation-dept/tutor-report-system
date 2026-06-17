"""メール送信キュー（アウトボックス）とドレイナの単体・結合テスト（新システム）。

要点:
- enqueue_mail は work_mail_outbox に pending 行を作るだけ（即時送信しない）。
- drain_outbox は MAIL_BACKEND=smtp のときだけ動き、1通ずつ順次送信して sent にする。
  console（既定・テスト）では何もしない＝実送信ゼロ。
- 送信失敗時は試行回数を加算し、上限で failed として打ち切る。
- ワークフロー/招待/パスワードリセットは送信キューへ投函される（即時送信しない）。

※実 SMTP は一切呼ばない（_deliver / smtplib をフェイクに差し替える）。
"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.shared import User
from app.models.work import WorkMailOutbox
from app.services import mailer
from tests.conftest import TEST_ENGINE, TestSession


def _fresh_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def _pending(engine):
    with Session(engine) as session:
        return list(session.scalars(select(WorkMailOutbox).order_by(WorkMailOutbox.created_at)))


def test_enqueue_mail_creates_pending_row():
    engine = _fresh_engine()
    with Session(engine) as session:
        mailer.enqueue_mail(session, "a@example.com", "件名", "本文")
    rows = _pending(engine)
    assert len(rows) == 1
    assert rows[0].to_email == "a@example.com"
    assert rows[0].status == "pending"
    assert rows[0].sent_at is None


def test_drain_console_is_noop(monkeypatch):
    # console（テスト既定）では実送信せず、pending のまま（drain は 0 件）
    monkeypatch.setattr(settings, "MAIL_BACKEND", "console")
    engine = _fresh_engine()
    with Session(engine) as session:
        mailer.enqueue_mail(session, "a@example.com", "S", "B")
    sent = mailer.drain_outbox(engine_override=engine)
    assert sent == 0
    assert _pending(engine)[0].status == "pending"


def test_drain_smtp_sends_in_order(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "MAIL_SEND_INTERVAL_SECONDS", 0)  # テストでは待機しない
    delivered: list = []
    monkeypatch.setattr(mailer, "_deliver", lambda to, subj, body: delivered.append(to))

    engine = _fresh_engine()
    with Session(engine) as session:
        mailer.enqueue_mail(session, "first@example.com", "S1", "B1")
        mailer.enqueue_mail(session, "second@example.com", "S2", "B2")

    sent = mailer.drain_outbox(engine_override=engine)
    assert sent == 2  # 1通ずつ順次処理して2通とも送信
    assert set(delivered) == {"first@example.com", "second@example.com"}
    rows = _pending(engine)
    assert all(r.status == "sent" and r.sent_at is not None for r in rows)


def test_drain_respects_batch_max(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "MAIL_SEND_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(settings, "MAIL_OUTBOX_BATCH_MAX", 2)
    monkeypatch.setattr(mailer, "_deliver", lambda *a: None)

    engine = _fresh_engine()
    with Session(engine) as session:
        for i in range(5):
            mailer.enqueue_mail(session, f"u{i}@example.com", "S", "B")

    assert mailer.drain_outbox(engine_override=engine) == 2  # 1回で最大2通
    statuses = [r.status for r in _pending(engine)]
    assert statuses.count("sent") == 2 and statuses.count("pending") == 3


def test_drain_failure_retries_then_fails(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "MAIL_SEND_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(settings, "MAIL_MAX_ATTEMPTS", 2)

    def boom(*args):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(mailer, "_deliver", boom)

    engine = _fresh_engine()
    with Session(engine) as session:
        mailer.enqueue_mail(session, "x@example.com", "S", "B")

    assert mailer.drain_outbox(engine_override=engine) == 0
    row = _pending(engine)[0]
    assert row.status == "pending" and row.attempts == 1  # 1回目失敗 → 再試行待ち

    assert mailer.drain_outbox(engine_override=engine) == 0
    row = _pending(engine)[0]
    assert row.status == "failed" and row.attempts == 2  # 上限到達で打ち切り


# ---------------------------------------------------------------------------
# 結合: API はメールを即時送信せず送信キューへ投函する
# ---------------------------------------------------------------------------

def test_forgot_password_enqueues_instead_of_sending():
    from fastapi.testclient import TestClient
    from app.core.security import hash_password
    from app.main import app

    with TestSession() as session:
        session.add(
            User(
                email="reset@new.example.com",
                role="tutor",
                roles=["tutor"],
                display_name="講師",
                password_hash=hash_password("Passw0rd!"),
                allowed_systems=["new"],
            )
        )
        session.commit()

    client = TestClient(app)
    res = client.post("/api/auth/forgot-password", json={"email": "reset@new.example.com"})
    assert res.status_code == 200, res.text

    rows = _pending(TEST_ENGINE)
    assert any(r.to_email == "reset@new.example.com" and r.status == "pending" for r in rows)
