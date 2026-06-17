"""メール送信キュー（アウトボックス）とバックグラウンド・ドレイナ。

設計方針（即時送信をやめ、投函→順次送信に変更）:
- 通知は即時送信せず、まず work_mail_outbox へ投函(enqueue_mail)する。リクエストは
  DBへ1行書くだけで即座に返る（SMTPの遅延・失敗がリクエストに波及しない）。
- バックグラウンドのドレイナ(drain_outbox)が「1通ずつ・送信間隔(MAIL_SEND_INTERVAL_SECONDS)
  をあけて」順次送信する。これにより一括操作・月末ラッシュでの同時送信／短時間連打を防ぎ、
  SMTPアカウント（フリーメール等）のスパム判定・ロックを回避する。
- 既存システムと同一SMTPアカウントを共有するため、PostgreSQLのアドバイザリロックで
  「常に1プロセス・1スレッドだけが送信中」になるよう全体を直列化する（同時送信を完全に防止）。
- MAIL_BACKEND=console（テスト/CI既定）では実送信せずログ出力のみ＝自動テストで実メールを
  高速・大量に送ることを構造的に防ぐ。本番/開発(MailHog)は smtp。
"""
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine as default_engine
from app.models.work import WorkMailOutbox

logger = logging.getLogger(__name__)

# 既存システム(backend)と「同一の値」にすること。同一SMTPアカウントを共有する2プロセスを
# 1つのアドバイザリロックで直列化し、同時送信を完全に防ぐためのキー。
MAIL_ADVISORY_LOCK_KEY = 472408301


def enqueue_mail(db: Session, to_email: str, subject: str, body: str) -> WorkMailOutbox:
    """メールを送信キュー（アウトボックス）へ投函する。実送信はドレイナが後で行う。

    呼び出し側の主処理コミット後に呼ばれる前提のため、ここで commit して確実に永続化する。
    """
    row = WorkMailOutbox(to_email=to_email, subject=subject, body=body, status="pending")
    db.add(row)
    db.commit()
    return row


def _send_via_smtp(to_email: str, subject: str, body: str) -> None:
    """同期SMTPで1通送信する（ドレイナはバックグラウンドスレッドのため同期 smtplib を使う）。"""
    message = EmailMessage()
    message["From"] = settings.NEW_SMTP_FROM
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    tls = (settings.SMTP_TLS or "none").lower()
    if tls == "ssl":  # 暗黙TLS（通常465番）
        server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
    try:
        if tls == "starttls":  # STARTTLS（通常587番）
            server.starttls()
        if settings.SMTP_USERNAME:
            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - クローズ失敗は送信成否に影響させない
            pass


def _deliver(to_email: str, subject: str, body: str) -> None:
    """MAIL_BACKEND に従って配信する。console はログ出力のみ（実送信しない）。"""
    if (settings.MAIL_BACKEND or "console").lower() == "smtp":
        _send_via_smtp(to_email, subject, body)
    else:
        logger.info("[MAIL:console] 送信スキップ to=%s subject=%s", to_email, subject)


def drain_outbox(engine_override=None) -> int:
    """送信待ちメールを1通ずつ、送信間隔をあけて順次送信する。送信できた件数を返す。

    - MAIL_BACKEND が smtp 以外（console/テスト）のときは何もしない＝実送信ゼロを保証。
    - PostgreSQL ではアドバイザリロックを取得できたプロセスだけが送信し、もう一方は次回に回す
      （2プロセス同時送信を完全に防止）。SQLite（テスト）では単一プロセス前提でロック省略。
    - 1通ごとに MAIL_SEND_INTERVAL_SECONDS 秒あけ、1回の実行で最大 MAIL_OUTBOX_BATCH_MAX 通。
    - 送信失敗時はその実行を打ち切り（連続失敗で連打しない）、次回に再試行する。
    """
    if (settings.MAIL_BACKEND or "console").lower() != "smtp":
        return 0

    engine = engine_override or default_engine
    interval = max(0, int(settings.MAIL_SEND_INTERVAL_SECONDS))
    batch_max = max(1, int(settings.MAIL_OUTBOX_BATCH_MAX))
    max_attempts = max(1, int(settings.MAIL_MAX_ATTEMPTS))
    table = WorkMailOutbox.__tablename__

    conn = engine.connect()
    is_pg = conn.dialect.name == "postgresql"
    try:
        if is_pg:
            locked = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": MAIL_ADVISORY_LOCK_KEY}
            ).scalar()
            if not locked:
                return 0  # 他プロセスが送信中。今回は何もせず次回に回す。
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
                # 次の1通があれば送信間隔をあける（最後の1通の後は待たない）
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
