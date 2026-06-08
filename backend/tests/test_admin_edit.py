from datetime import date, time

from app.models import Assignment, LessonReport, ReportEvent, User
from tests.conftest import token


def _make_report(db, status="received"):
    assignment = db.query(Assignment).first()
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        parent_id=parent.id,
        lesson_date=date(2026, 6, 15),
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        subject="数学",
        content="二次関数",
        status=status,
        target_month="2026-06",
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def _patch_email(monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.services.notification_service.EmailChannel.send", fake_send)
    return sent


def test_receiver_can_edit_received_report_and_notifies(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="received")
    rid = str(report.id)
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.patch(f"/api/reports/{rid}/admin-edit", headers=headers, json={
        "lesson_date": "2026-06-15", "start_time": "18:30", "end_time": "19:00",
        "break_minutes": 10, "subject": "数学", "content": "二次関数と平方完成",
    })
    assert res.status_code == 200, res.text
    db.refresh(report)
    assert report.content == "二次関数と平方完成"
    assert report.break_minutes == 10
    assert report.status == "received"  # ステータスは不変（再承認不要）

    recipients = {m[0] for m in sent}
    assert "tutor@example.com" in recipients
    assert "parent@example.com" in recipients
    body = sent[-1][2]
    assert "修正内容" in body
    assert "二次関数と平方完成" in body

    events = db.query(ReportEvent).filter(ReportEvent.report_id == report.id, ReportEvent.action == "receiver_edit").all()
    assert len(events) == 1
    assert "修正項目" in (events[0].comment or "")


def test_receiver_cannot_edit_non_pipeline_report(client, db, monkeypatch):
    _patch_email(monkeypatch)
    report = _make_report(db, status="admin_approved")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.patch(f"/api/reports/{report.id}/admin-edit", headers=headers,
                       json={"content": "変更"})
    assert res.status_code == 409


def test_non_receiver_cannot_admin_edit(client, db, monkeypatch):
    _patch_email(monkeypatch)
    report = _make_report(db, status="received")
    headers = {"Authorization": f"Bearer {token(client, 'reviewer@example.com')}"}
    res = client.patch(f"/api/reports/{report.id}/admin-edit", headers=headers,
                       json={"content": "変更"})
    assert res.status_code == 403


def test_no_change_sends_no_email(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="submitted_to_admin")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.patch(f"/api/reports/{report.id}/admin-edit", headers=headers, json={
        "lesson_date": "2026-06-15", "start_time": "18:00", "end_time": "19:00",
        "break_minutes": 0, "subject": "数学", "content": "二次関数",
    })
    assert res.status_code == 200, res.text
    assert sent == []  # 変更がなければ通知しない
    edits = db.query(ReportEvent).filter(ReportEvent.report_id == report.id, ReportEvent.action == "receiver_edit").all()
    assert edits == []  # 変更がなければ履歴も残さない
