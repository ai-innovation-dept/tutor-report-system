# === Phase 7: 通知・リマインダー START ===
from datetime import date, datetime, time, timezone

from app.models import Assignment, LessonReport, Notification, ReportStatus
from app.services.reminder_service import enqueue_approval_reminders, enqueue_month_end_reminders, is_reminder_day


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


def test_enqueue_approval_reminders_uses_assignment_settings(client, db):
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 2
    assignment.reminder_count = 2
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date(2026, 5, 1),
        start_time=time(18, 0),
        end_time=time(19, 0),
        content="lesson",
        target_month="2026-05",
        status=ReportStatus.awaiting_parent_approval.value,
        submitted_to_parent_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    db.add(report)
    db.commit()

    assert enqueue_approval_reminders(db, date(2026, 5, 3)) == 1
    notification = db.query(Notification).one()
    assert notification.type == "reminder_approval"
    assert "承認のお願い" in notification.subject


def test_approval_reminders_are_endless_regardless_of_count(client, db):
    # 保護者が承認するまでエンドレスに送る。旧 reminder_count の上限は無視される。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 2
    assignment.reminder_count = 1  # 旧仕様なら1回で打ち切りだが、現仕様では無視
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
        submitted_to_parent_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    ))
    db.commit()

    # 提出から20日後（間隔2日で10回目相当）でも送信される
    assert enqueue_approval_reminders(db, date(2026, 5, 21)) == 1


def test_approval_reminder_stops_after_parent_approves(client, db):
    # 承認後（awaiting_parent_approval 以外）はリマインドが送られない。
    assignment = db.query(Assignment).first()
    assignment.reminder_enabled = True
    assignment.reminder_days_after = 2
    db.add(LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date(2026, 5, 1),
        start_time=time(18, 0),
        end_time=time(19, 0),
        content="lesson",
        target_month="2026-05",
        status=ReportStatus.parent_approved.value,
        submitted_to_parent_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    ))
    db.commit()

    assert enqueue_approval_reminders(db, date(2026, 5, 21)) == 0
# === Phase 7 END ===
