# === Phase 7: 通知・リマインダー START ===
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import aiosmtplib
from email.message import EmailMessage
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Notification, User


def _smtp_send_kwargs() -> dict:
    """settings から aiosmtplib.send への接続パラメータ（ホスト/ポート/認証/TLS）を組み立てる。

    本番の外部SMTPサービス（認証＋TLS必須）と開発のMailHog（認証/TLSなし）を同じコードで扱う。
    認証は SMTP_USERNAME が設定されている場合のみ付与する。
    """
    tls = (settings.smtp_tls or "none").lower()
    kwargs: dict = {
        "hostname": settings.smtp_host,
        "port": settings.smtp_port,
        "use_tls": tls == "ssl",         # 暗黙TLS（通常465番）
        "start_tls": tls == "starttls",  # STARTTLS（通常587番）
    }
    if settings.smtp_username:
        kwargs["username"] = settings.smtp_username
        kwargs["password"] = settings.smtp_password
    return kwargs


class NotificationChannel(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, body: str) -> None:
        raise NotImplementedError


class EmailChannel(NotificationChannel):
    async def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await aiosmtplib.send(message, **_smtp_send_kwargs())


def _render_email_template(template_name: str, context: dict) -> str:
    template_path = Path(__file__).resolve().parents[1] / "templates" / "email" / template_name
    template = template_path.read_text(encoding="utf-8")
    return template.format(**context)


async def send_email_notification(to_email: str, subject: str, template_name: str, context: dict) -> None:
    body = _render_email_template(template_name, context)
    await EmailChannel().send(to_email, subject, body)


# LINE extension point: implement LineChannel(NotificationChannel) and select it by notification.channel.
async def send_pending(notification: Notification, user: User) -> None:
    channel: NotificationChannel = EmailChannel()
    await channel.send(user.email, notification.subject, notification.body)
    notification.sent_at = datetime.now(timezone.utc)


def enqueue(db: Session, user_id: UUID, notification_type: str, subject: str, body: str, report_id: UUID | None = None, channel: str = "email") -> Notification:
    notification = Notification(
        user_id=user_id,
        report_id=report_id,
        channel=channel,
        type=notification_type,
        subject=subject,
        body=body,
    )
    db.add(notification)
    return notification
# === Phase 7 END ===
