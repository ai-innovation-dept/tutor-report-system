# === Phase 7: 通知・リマインダー START ===
from datetime import date, datetime, time, timedelta, timezone

from app.models import Assignment, LessonReport, Notification, ReportStatus
from app.services.reminder_service import (
    _send_pending_approval_emails,
    enqueue_approval_reminders,
    enqueue_month_end_reminders,
    is_reminder_day,
)


def test_reminder_day():
    assert is_reminder_day(date(2026, 5, 29), 3)
    assert not is_reminder_day(date(2026, 5, 20), 3)


def test_enqueue_reminders(client, db):
    assignment = db.query(Assignment).first()
    db.add(LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date(2026, 5, 1),
        start_time=time(18, 0),
        end_time=time(19, 0),
        content="lesson",
        target_month="2026-05",
        status=ReportStatus.awaiting_parent_approval.value,
    ))
    db.commit()
    assert enqueue_month_end_reminders(db, date(2026, 5, 29)) == 1


def _awaiting_report(db, assignment, submitted_at, status=ReportStatus.awaiting_parent_approval.value):
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date(2026, 5, 1),
        start_time=time(18, 0),
        end_time=time(19, 0),
        content="lesson",
        target_month="2026-05",
        status=status,
        submitted_to_parent_at=submitted_at,
    )
    db.add(report)
    db.commit()
    return report


def test_approval_reminder_due_by_minutes(client, db):
    # reminder_days_after は「分」として解釈する（テスト用 分単位モード）
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 10
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _awaiting_report(db, assignment, now - timedelta(minutes=10))

    assert enqueue_approval_reminders(db, now) == 1
    notification = db.query(Notification).filter(Notification.type == "reminder_approval").one()
    assert "承認のお願い" in notification.subject


def test_approval_reminder_not_due_before_interval(client, db):
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 10
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _awaiting_report(db, assignment, now - timedelta(minutes=5))

    assert enqueue_approval_reminders(db, now) == 0


def test_approval_reminder_endless_by_minutes(client, db):
    # 間隔10分・経過25分 → 本来2回送信されているべき。1回1件ずつ作成し、上限なくエンドレス。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 10
    assignment.reminder_count = 1  # 旧上限は無視される
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _awaiting_report(db, assignment, now - timedelta(minutes=25))

    assert enqueue_approval_reminders(db, now) == 1
    assert enqueue_approval_reminders(db, now) == 1
    assert enqueue_approval_reminders(db, now) == 0  # 2件作成済みで打ち止め（次は次の10分到来後）
    assert db.query(Notification).filter(Notification.type == "reminder_approval").count() == 2


def test_approval_reminder_stops_after_parent_approves(client, db):
    # 承認後（awaiting_parent_approval 以外）はリマインドが作成されない。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 1
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _awaiting_report(db, assignment, now - timedelta(minutes=60), status=ReportStatus.parent_approved.value)

    assert enqueue_approval_reminders(db, now) == 0


def test_approval_reminder_emails_are_sent(client, db, monkeypatch):
    # 作成された承認リマインド通知が実際にメール送信され、sent_at が記録される。
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.services.notification_service.EmailChannel.send", fake_send)
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 1
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    _awaiting_report(db, assignment, now - timedelta(minutes=5))

    assert enqueue_approval_reminders(db, now) == 1
    db.flush()
    assert _send_pending_approval_emails(db) == 1
    db.commit()

    assert len(sent) == 1
    notification = db.query(Notification).filter(Notification.type == "reminder_approval").one()
    assert notification.sent_at is not None
# === Phase 7 END ===
