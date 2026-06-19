"""差戻し中の報告書を講師が修正・保存したときの通知（改修③）のテスト。

受付/事務の編集通知(notify_report_modified)と対になる、講師→運営方向の通知を確認する。
差戻した操作者（直近の差戻しイベントの actor）へ1通、差分付きで届く。編集者である講師には送らない。
"""
from datetime import date, time

from app.core.time import get_current_jst_month
from app.models import Assignment, LessonReport, ReportEvent, User
from tests.conftest import token


def _current_month_date(day: int) -> tuple[str, date]:
    month = get_current_jst_month()
    year, mon = map(int, month.split("-"))
    return month, date(year, mon, day)


def _patch_email(monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.services.notification_service.EmailChannel.send", fake_send)
    return sent


def _make_report(db, status, day, content="二次関数", returner_email=None):
    month, lesson_date = _current_month_date(day)
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
        target_month=month,
    )
    db.add(report)
    db.flush()
    if returner_email:
        returner = db.query(User).filter(User.email == returner_email).one()
        db.add(ReportEvent(
            report_id=report.id, actor_id=returner.id, action="return_from_receiver",
            from_status="received", to_status="returned_to_tutor", comment="開始時刻を直してください",
        ))
    db.commit()
    db.refresh(report)
    return report


def test_tutor_edit_returned_notifies_returner(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="returned_to_tutor", day=15, returner_email="receiver@example.com")
    headers = {"Authorization": f"Bearer {token(client, 'tutor@example.com')}"}
    # 改修②: 開始時刻の修正、改修③: 保存できること
    res = client.patch(f"/api/reports/{report.id}", headers=headers, json={"start_time": "17:30"})
    assert res.status_code == 200, res.text
    db.refresh(report)
    assert report.start_time == time(17, 30)

    # 差戻した受付へ通知。編集者である講師には送らない。
    recipients = {m[0] for m in sent}
    assert "receiver@example.com" in recipients
    assert "tutor@example.com" not in recipients
    body = sent[-1][2]
    assert "修正内容" in body
    assert "開始時刻" in body  # 差分項目名

    # 「何を何に変えたか」が監査履歴(tutor_edit イベントの comment)に保存されている
    events = db.query(ReportEvent).filter(
        ReportEvent.report_id == report.id, ReportEvent.action == "tutor_edit"
    ).all()
    assert len(events) == 1
    assert "開始時刻" in (events[0].comment or "")
    assert "18:00" in events[0].comment and "17:30" in events[0].comment


def test_tutor_edit_returned_no_change_no_notify(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="returned_to_tutor", day=16, returner_email="receiver@example.com")
    headers = {"Authorization": f"Bearer {token(client, 'tutor@example.com')}"}
    # 値を変えない保存（コメントもなし）では通知しない
    res = client.patch(f"/api/reports/{report.id}", headers=headers, json={"subject": "数学"})
    assert res.status_code == 200, res.text
    assert sent == []
    # 変更がなければ tutor_edit イベントも残さない
    assert db.query(ReportEvent).filter(
        ReportEvent.report_id == report.id, ReportEvent.action == "tutor_edit"
    ).count() == 0


def test_tutor_edit_draft_does_not_notify(client, db, monkeypatch):
    sent = _patch_email(monkeypatch)
    report = _make_report(db, status="draft", day=17)
    headers = {"Authorization": f"Bearer {token(client, 'tutor@example.com')}"}
    res = client.patch(f"/api/reports/{report.id}", headers=headers, json={"start_time": "17:30"})
    assert res.status_code == 200, res.text
    assert sent == []  # 下書きの編集では通知しない
