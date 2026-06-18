from datetime import date, time

from app.models import Assignment, LessonReport, ReportEvent, User
from tests.conftest import token


def _make_report(db, status="received", lesson_date=date(2026, 6, 15), content="二次関数"):
    assignment = db.query(Assignment).first()
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        parent_id=parent.id,
        lesson_date=lesson_date,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        subject="数学",
        content=content,
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


def _line(report, **overrides):
    line = {
        "id": str(report.id),
        "lesson_date": report.lesson_date.isoformat(),
        "start_time": report.start_time.strftime("%H:%M"),
        "end_time": report.end_time.strftime("%H:%M"),
        "break_minutes": report.break_minutes,
        "subject": report.subject,
        "content": report.content,
    }
    line.update(overrides)
    return line


def _payload(report, lines, comment=None):
    return {
        "assignment_id": str(report.assignment_id),
        "tutor_id": str(report.tutor_id),
        "target_month": report.target_month,
        "lines": lines,
        "comment": comment,
    }


def test_receiver_can_bulk_edit_and_notifies(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="received")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.post(
        "/api/reports/admin-edit-bulk",
        headers=headers,
        json=_payload(report, [_line(report, start_time="18:30", break_minutes=10, content="二次関数と平方完成")]),
    )
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


def test_bulk_edit_multiple_days_sends_single_notification(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report1 = _make_report(db, status="received", lesson_date=date(2026, 6, 15), content="A")
    report2 = _make_report(db, status="received", lesson_date=date(2026, 6, 16), content="B")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.post(
        "/api/reports/admin-edit-bulk",
        headers=headers,
        json=_payload(
            report1,
            [_line(report1, content="A改"), _line(report2, content="B改")],
        ),
    )
    assert res.status_code == 200, res.text
    db.refresh(report1)
    db.refresh(report2)
    assert report1.content == "A改"
    assert report2.content == "B改"
    # 月内複数日を編集しても通知は宛先ごとに1通ずつ（講師1・保護者1＝計2）
    assert sum(1 for m in sent if m[0] == "tutor@example.com") == 1
    assert sum(1 for m in sent if m[0] == "parent@example.com") == 1


def test_receiver_cannot_edit_non_pipeline_report(client, db, monkeypatch):
    _patch_email(monkeypatch)
    report = _make_report(db, status="admin_approved")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.post(
        "/api/reports/admin-edit-bulk",
        headers=headers,
        json=_payload(report, [_line(report, content="変更")]),
    )
    assert res.status_code == 409


def test_non_receiver_cannot_bulk_edit(client, db, monkeypatch):
    _patch_email(monkeypatch)
    report = _make_report(db, status="received")
    headers = {"Authorization": f"Bearer {token(client, 'reviewer@example.com')}"}
    res = client.post(
        "/api/reports/admin-edit-bulk",
        headers=headers,
        json=_payload(report, [_line(report, content="変更")]),
    )
    assert res.status_code == 403


def test_no_change_sends_no_email(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="submitted_to_admin")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.post(
        "/api/reports/admin-edit-bulk",
        headers=headers,
        json=_payload(report, [_line(report)]),  # 変更なし
    )
    assert res.status_code == 200, res.text
    assert sent == []  # 変更がなければ通知しない
    edits = db.query(ReportEvent).filter(ReportEvent.report_id == report.id, ReportEvent.action == "receiver_edit").all()
    assert edits == []  # 変更がなければ履歴も残さない


def test_comment_only_notifies_and_logs(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="received")
    headers = {"Authorization": f"Bearer {token(client, 'receiver@example.com')}"}
    res = client.post(
        "/api/reports/admin-edit-bulk",
        headers=headers,
        json=_payload(report, [_line(report)], comment="軽微な補足です"),  # 明細変更なし・コメントのみ
    )
    assert res.status_code == 200, res.text
    recipients = {m[0] for m in sent}
    assert "tutor@example.com" in recipients
    body = sent[-1][2]
    assert "軽微な補足です" in body
    edits = db.query(ReportEvent).filter(ReportEvent.report_id == report.id, ReportEvent.action == "receiver_edit").all()
    assert len(edits) == 1
