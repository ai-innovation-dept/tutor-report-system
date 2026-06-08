# === Phase 7: 通知・リマインダー START ===
from datetime import date, datetime, time, timezone

from app.models import Assignment, LessonReport, Notification, ReportStatus, User
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


def test_approval_reminder_due_by_days(client, db):
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 2
    _awaiting_report(db, assignment, datetime(2026, 5, 1, tzinfo=timezone.utc))

    # 提出から2日後（間隔2日）→ 1件
    assert enqueue_approval_reminders(db, date(2026, 5, 3)) == 1
    notification = db.query(Notification).filter(Notification.type == "reminder_approval").one()
    assert "承認のお願い" in notification.subject


def test_approval_reminder_not_due_before_interval(client, db):
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 3
    _awaiting_report(db, assignment, datetime(2026, 5, 1, tzinfo=timezone.utc))

    # 提出から1日後（間隔3日）→ まだ送らない
    assert enqueue_approval_reminders(db, date(2026, 5, 2)) == 0


def test_approval_reminder_endless_regardless_of_count(client, db):
    # 間隔2日・経過4日 → 本来2回。1回1件ずつ作成し、旧 reminder_count 上限は無視。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 2
    assignment.reminder_count = 1  # 旧上限は無視される
    _awaiting_report(db, assignment, datetime(2026, 5, 1, tzinfo=timezone.utc))

    assert enqueue_approval_reminders(db, date(2026, 5, 5)) == 1
    assert enqueue_approval_reminders(db, date(2026, 5, 5)) == 1
    assert enqueue_approval_reminders(db, date(2026, 5, 5)) == 0  # 2件作成済みで打ち止め
    assert db.query(Notification).filter(Notification.type == "reminder_approval").count() == 2


def test_approval_reminder_stops_after_parent_approves(client, db):
    # 承認後（awaiting_parent_approval 以外）はリマインドが作成されない。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 1
    _awaiting_report(db, assignment, datetime(2026, 5, 1, tzinfo=timezone.utc), status=ReportStatus.parent_approved.value)

    assert enqueue_approval_reminders(db, date(2026, 5, 21)) == 0


def test_approval_reminder_body_mentions_tutor_and_student(client, db):
    # 文言に講師名・生徒名が含まれること（誰からの依頼か伝わること）を確認。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 1
    tutor = db.get(User, assignment.tutor_id)
    _awaiting_report(db, assignment, datetime(2026, 5, 1, tzinfo=timezone.utc))

    assert enqueue_approval_reminders(db, date(2026, 5, 2)) == 1
    notification = db.query(Notification).filter(Notification.type == "reminder_approval").one()
    assert tutor.display_name in notification.body
    assert assignment.student_name in notification.body


def test_approval_reminder_emails_are_sent(client, db, monkeypatch):
    # 作成された承認リマインド通知が実際にメール送信され、sent_at が記録される。
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.services.notification_service.EmailChannel.send", fake_send)
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 1
    _awaiting_report(db, assignment, datetime(2026, 5, 1, tzinfo=timezone.utc))

    assert enqueue_approval_reminders(db, date(2026, 5, 2)) == 1
    db.flush()
    assert _send_pending_approval_emails(db) == 1
    db.commit()

    assert len(sent) == 1
    notification = db.query(Notification).filter(Notification.type == "reminder_approval").one()
    assert notification.sent_at is not None
# === Phase 7 END ===
