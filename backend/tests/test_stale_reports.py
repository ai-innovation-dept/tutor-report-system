from datetime import datetime, time, timedelta

from app.core.security import hash_password
from app.core.time import get_current_jst, get_current_jst_date
from app.models import Assignment, LessonReport, Notification, ReportStatus, User
from app.services.reminder_service import daily_stale_check
from app.services.report_service import close_report, get_stale_reports
from tests.conftest import token


def _previous_month_date():
    first = get_current_jst_date().replace(day=1)
    previous_last = first - timedelta(days=1)
    return previous_last.replace(day=1)


def _make_report(db, assignment: Assignment, status: str = ReportStatus.returned_to_tutor.value, stale_since=None) -> LessonReport:
    lesson_date = _previous_month_date()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=lesson_date,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="stale lesson",
        target_month=lesson_date.strftime("%Y-%m"),
        status=status,
        stale_since=stale_since,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def test_get_stale_reports_returns_old_unfinished(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)

    stale = get_stale_reports(db)

    assert [item.id for item in stale] == [report.id]


def test_get_stale_reports_excludes_approved(client, db):
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, status=ReportStatus.admin_approved.value)

    assert get_stale_reports(db) == []


def test_get_stale_reports_excludes_closed(client, db):
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, status=ReportStatus.closed.value)

    assert get_stale_reports(db) == []


def test_close_report_records_reason_and_actor(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)
    master = db.query(User).filter(User.email == "master@example.com").one()

    closed = close_report(report.id, "保護者同意済み対応不要", master, db)

    assert closed.status == ReportStatus.closed.value
    assert closed.closed_at is not None
    assert closed.closed_by == master.id
    assert closed.close_reason == "保護者同意済み対応不要"


def test_closed_report_detail_shows_close_reason(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)
    master_token = token(client, "master@example.com")
    closed = client.post(
        f"/api/reports/{report.id}/close",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"close_reason": "最終承認済み（システム外で処理）"},
    )
    assert closed.status_code == 200

    detail = client.get(f"/api/reports/{report.id}", headers={"Authorization": f"Bearer {master_token}"})

    assert detail.status_code == 200
    assert detail.json()["status"] == ReportStatus.closed.value
    assert detail.json()["close_reason"] == "最終承認済み（システム外で処理）"
    assert detail.json()["closed_at"] is not None
    assert detail.json()["closed_by_name"] == "Master"


def test_non_closed_report_detail_hides_close_reason(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)
    tutor_token = token(client, "tutor@example.com")

    detail = client.get(f"/api/reports/{report.id}", headers={"Authorization": f"Bearer {tutor_token}"})

    assert detail.status_code == 200
    assert detail.json()["status"] != ReportStatus.closed.value
    assert detail.json()["close_reason"] is None
    assert detail.json()["closed_at"] is None
    assert detail.json()["closed_by_name"] is None


def test_close_report_does_not_delete_record(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)
    master = db.query(User).filter(User.email == "master@example.com").one()

    close_report(report.id, "重複報告書のため無効化", master, db)

    assert db.get(LessonReport, report.id) is not None


def test_close_report_requires_reason(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)
    master = db.query(User).filter(User.email == "master@example.com").one()

    try:
        close_report(report.id, " ", master, db)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
    else:
        raise AssertionError("close_report should require a reason")


def test_close_report_is_idempotent(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, status=ReportStatus.closed.value)
    master = db.query(User).filter(User.email == "master@example.com").one()

    closed = close_report(report.id, "", master, db)

    assert closed.id == report.id
    assert closed.status == ReportStatus.closed.value


def test_stale_count_endpoint_returns_correct_count(client, db):
    assignment = db.query(Assignment).first()
    _make_report(db, assignment)
    _make_report(db, assignment, status=ReportStatus.admin_approved.value)
    tutor_token = token(client, "tutor@example.com")

    res = client.get("/api/stale-count", headers={"Authorization": f"Bearer {tutor_token}"})

    assert res.status_code == 200
    assert res.json() == {"count": 1}


def test_stale_count_tutor_only_sees_own_reports(client, db):
    assignment = db.query(Assignment).first()
    other_tutor = User(
        email="other-tutor@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="Other Tutor",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(other_tutor)
    db.flush()
    other_assignment = Assignment(tutor_id=other_tutor.id, parent_id=assignment.parent_id, student_name="Other Student")
    db.add(other_assignment)
    db.flush()
    _make_report(db, assignment)
    _make_report(db, other_assignment)
    tutor_token = token(client, "tutor@example.com")

    res = client.get("/api/stale-count", headers={"Authorization": f"Bearer {tutor_token}"})

    assert res.status_code == 200
    assert res.json() == {"count": 1}


def test_stale_count_closed_report_not_counted(client, db):
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, status=ReportStatus.closed.value)
    tutor_token = token(client, "tutor@example.com")

    res = client.get("/api/stale-count", headers={"Authorization": f"Bearer {tutor_token}"})

    assert res.status_code == 200
    assert res.json() == {"count": 0}


def test_stale_count_staff_sees_all_reports(client, db):
    assignment = db.query(Assignment).first()
    other_tutor = User(
        email="staff-count-tutor@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="Staff Count Tutor",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(other_tutor)
    db.flush()
    other_assignment = Assignment(tutor_id=other_tutor.id, parent_id=assignment.parent_id, student_name="Staff Count Student")
    db.add(other_assignment)
    db.flush()
    _make_report(db, assignment)
    _make_report(db, other_assignment)
    receiver_token = token(client, "receiver@example.com")

    res = client.get("/api/stale-count", headers={"Authorization": f"Bearer {receiver_token}"})

    assert res.status_code == 200
    assert res.json() == {"count": 2}


def test_stale_count_parent_only_sees_own_children(client, db):
    assignment = db.query(Assignment).first()
    other_parent = User(
        email="stale-other-parent@example.com",
        role="parent",
        roles=["parent"],
        display_name="Other Parent",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(other_parent)
    db.flush()
    other_assignment = Assignment(tutor_id=assignment.tutor_id, parent_id=other_parent.id, student_name="Other Parent Student")
    db.add(other_assignment)
    db.flush()
    _make_report(db, assignment)
    _make_report(db, other_assignment)
    parent_token = token(client, "parent@example.com")

    res = client.get("/api/stale-count", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert res.json() == {"count": 1}


def test_close_endpoint_forbidden_for_tutor(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)
    tutor_token = token(client, "tutor@example.com")

    res = client.post(
        f"/api/reports/{report.id}/close",
        headers={"Authorization": f"Bearer {tutor_token}"},
        json={"close_reason": "保護者同意済み対応不要"},
    )

    assert res.status_code == 403


def test_daily_batch_sets_stale_since(client, db):
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment)

    daily_stale_check(db)
    db.refresh(report)

    assert report.stale_since is not None


def test_daily_batch_does_not_overwrite_stale_since(client, db):
    assignment = db.query(Assignment).first()
    original = datetime(2026, 5, 1, 9, 0, 0)
    report = _make_report(db, assignment, stale_since=original)

    daily_stale_check(db)
    db.refresh(report)

    assert report.stale_since == original


def test_daily_batch_enqueues_notifications_after_threshold(client, db, monkeypatch):
    monkeypatch.setenv("STALE_REMIND_DAYS", "0")
    monkeypatch.setenv("STALE_WARN_DAYS", "14")
    monkeypatch.setenv("STALE_ESCALATE_DAYS", "30")
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, stale_since=datetime.now() - timedelta(days=1))

    daily_stale_check(db)

    assert db.query(Notification).filter(Notification.type == "stale_report_remind").count() == 3
