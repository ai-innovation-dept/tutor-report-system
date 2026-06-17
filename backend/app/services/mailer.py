"""メール送信キュー（アウトボックス）とバックグラウンド・ドレイナ（既存システム）。

新システム(new_backend)の services/mailer.py と同じ設計。通知は即時送信せず
mail_outbox へ投函(enqueue_mail)し、ドレイナ(drain_outbox)が「1通ずつ・送信間隔を
あけて」順次送信する。同時送信／短時間連打によるSMTPアカウントのスパム判定・ロックを防ぐ。

既存システムと新システムは同一SMTPアカウント(.env共有)を使うため、両系で **同一の
アドバイザリロックキー** を使い、PostgreSQL 上で「常に1プロセスだけが送信中」になるよう
全体を直列化する（2システム横断で同時送信を完全防止）。

MAIL_BACKEND=console（テスト/CI既定）では実送信せずログ出力のみ＝自動テストで実メールを
高速・大量に送ることを構造的に防ぐ。本番/開発(MailHog)は smtp。
"""
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal, engine as default_engine
from app.models.entities import MailOutbox

logger = logging.getLogger(__name__)

# 新システム(new_backend)の MAIL_ADVISORY_LOCK_KEY と「同一の値」にすること。
# 同一SMTPアカウントを共有する両系プロセスを1つのロックで直列化し、同時送信を完全に防ぐ。
MAIL_ADVISORY_LOCK_KEY = 472408301


def enqueue_mail(to_email: str, subject: str, body: str) -> None:
    """メールを送信キュー（アウトボックス）へ投函する。実送信はドレイナが後で行う。

    送信箇所(EmailChannel.send 等)に DB セッションが渡らないため、短命の専用セッションで
    投函する（呼び出し側の主処理はコミット後に通知するため独立トランザクションで問題ない）。
    """
    db = SessionLocal()
    try:
        db.add(MailOutbox(to_email=to_email, subject=subject, body=body, status="pending"))
        db.commit()
    finally:
        db.close()


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    """同期SMTPで1通送信する（ドレイナはスケジューラのスレッドのため同期 smtplib を使う）。"""
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    tls = (settings.smtp_tls or "none").lower()
    if tls == "ssl":  # 暗黙TLS（通常465番）
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
    try:
        if tls == "starttls":  # STARTTLS（通常587番）
            server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - クローズ失敗は送信成否に影響させない
            pass


def _deliver(to_email: str, subject: str, body: str) -> None:
    """MAIL_BACKEND に従って配信する。console はログ出力のみ（実送信しない）。"""
    if (settings.mail_backend or "console").lower() == "smtp":
        _send_via_smtp(to_email, subject, body)
    else:
        logger.info("[MAIL:console] 送信スキップ to=%s subject=%s", to_email, subject)


def drain_outbox(engine_override=None) -> int:
    """送信待ちメールを1通ずつ、送信間隔をあけて順次送信する。送信できた件数を返す。

    - MAIL_BACKEND が smtp 以外（console/テスト）のときは何もしない＝実送信ゼロを保証。
    - PostgreSQL ではアドバイザリロックを取得できたプロセスだけが送信し、もう一方は次回に回す
      （既存/新システム横断で同時送信を完全防止）。SQLite（テスト）では単一プロセス前提で省略。
    - 1通ごとに mail_send_interval_seconds 秒あけ、1回の実行で最大 mail_outbox_batch_max 通。
    - 送信失敗時はその実行を打ち切り（連続失敗で連打しない）、次回に再試行する。
    """
    if (settings.mail_backend or "console").lower() != "smtp":
        return 0

    engine = engine_override or default_engine
    interval = max(0, int(settings.mail_send_interval_seconds))
    batch_max = max(1, int(settings.mail_outbox_batch_max))
    max_attempts = max(1, int(settings.mail_max_attempts))
    table = MailOutbox.__tablename__

    conn = engine.connect()
    is_pg = conn.dialect.name == "postgresql"
    try:
        if is_pg:
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": MAIL_ADVISORY_LOCK_KEY}
            ).scalar()
            if not locked:
                return 0  # 他プロセス（既存/新）が送信中。今回は何もせず次回に回す。
        sent = 0
        try:
            while sent < batch_max:
                row = conn.execute(
                    text(
                        f"SELECT id, to_email, subject, body, attempts FROM {table} "
                        "WHERE status = 'pending' ORDER BY created_at LIMIT 1"
                    )
                ).mappings().first()
                if row is None:
                    break
                try:
                    _deliver(row["to_email"], row["subject"], row["body"])
                    conn.execute(
                        text(f"UPDATE {table} SET status='sent', sent_at=:now WHERE id=:id"),
                        {"now": datetime.now(timezone.utc), "id": row["id"]},
                    )
                    conn.commit()
                    sent += 1
                except Exception as exc:  # noqa: BLE001 - 失敗を記録し、この実行は打ち切る
                    attempts = int(row["attempts"]) + 1
                    status = "failed" if attempts >= max_attempts else "pending"
                    conn.execute(
                        text(
                            f"UPDATE {table} SET attempts=:a, last_error=:e, status=:s WHERE id=:id"
                        ),
                        {"a": attempts, "e": str(exc)[:1000], "s": status, "id": row["id"]},
                    )
                    conn.commit()
                    logger.warning("mail send failed (attempt %s) to %s: %s", attempts, row["to_email"], exc)
                    break
                has_more = conn.execute(
                    text(f"SELECT 1 FROM {table} WHERE status='pending' LIMIT 1")
                ).first()
                if has_more is not None and interval:
                    time.sleep(interval)
        finally:
            if is_pg:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MAIL_ADVISORY_LOCK_KEY})
                conn.commit()
        return sent
    finally:
        conn.close()
