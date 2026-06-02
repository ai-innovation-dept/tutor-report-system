"""通知サービス（メール送信・通知レコード作成）。"""
import logging
from datetime import datetime, timezone

from app.core.config import settings

from sqlalchemy.orm import Session

from app.models.shared import User
from app.models.work import WorkNotification, WorkReport

logger = logging.getLogger(__name__)


def record_notification(
    db: Session,
    user: User,
    report: WorkReport,
    notif_type: str,
    subject: str,
    body: str,
) -> WorkNotification:
    notif = WorkNotification(
        user_id=user.id,
        report_id=report.id,
        type=notif_type,
        subject=subject,
        body=body,
    )
    db.add(notif)
    return notif


async def send_email(
    to: str,
    subject: str,
    body: str,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
) -> None:
    """DBへの記録なしで単体メールを送信する（招待・パスワードリセット用）。"""
    host = smtp_host or settings.SMTP_HOST
    port = smtp_port or settings.SMTP_PORT
    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = "noreply@work-system.local"
        msg["To"] = to
        await aiosmtplib.send(msg, hostname=host, port=port)
    except Exception as exc:
        logger.warning("mail send failed to %s: %s", to, exc)


async def send_notification(
    db: Session,
    user: User,
    report: WorkReport,
    notif_type: str,
    subject: str,
    body: str,
    smtp_host: str = "mailhog",
    smtp_port: int = 1025,
) -> None:
    notif = record_notification(db, user, report, notif_type, subject, body)
    try:
        import aiosmtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = "noreply@work-system.local"
        msg["To"] = user.email
        await aiosmtplib.send(msg, hostname=smtp_host, port=smtp_port)
        notif.sent_at = datetime.now(timezone.utc)
    except Exception as exc:
        logger.warning("mail send failed: %s", exc)
