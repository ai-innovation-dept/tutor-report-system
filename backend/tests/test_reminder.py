# === Phase 7: 通知・リマインダー START ===
from datetime import date, time

from app.models import Assignment, LessonReport, ReportStatus
from app.services.reminder_service import enqueue_month_end_reminders, is_reminder_day


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
# === Phase 7 END ===

