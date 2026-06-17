# === Phase 7: 通知・リマインダー START ===
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Notification, User
from app.services.mailer import enqueue_mail


class NotificationChannel(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, body: str) -> None:
        raise NotImplementedError


class EmailChannel(NotificationChannel):
    async def send(self, to: str, subject: str, body: str) -> None:
        # 即時送信せず送信キュー(mail_outbox)へ投函する。実送信はドレイナ(services/mailer.
        # drain_outbox)が1通ずつ間隔をあけて行う。同時送信・短時間連打を防ぐ。
        enqueue_mail(to, subject, body)


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
