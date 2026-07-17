"""学校の締め日通知設定＋締め日前【至急確認】メール（改修依頼 202607161140）のテスト。

- 設定API: ユーザ管理の学校詳細から 早期チェックON/OFF・通知日数・月ごとの締め日（年間）を保存。
- 日次ジョブ: 早期チェックONの学校のみ「締め日−N日〜締め日当日」の窓で1回だけ営業全員へ送信。
  全員承認済みの学校・締め日を過ぎた月は送らない。締め日変更で再送対象に戻る。
実メールは送らない（conftest で MAIL_BACKEND=console。投函先の WorkMailOutbox を検証する）。
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import (
    WorkAssignmentProfile,
    WorkMailOutbox,
    WorkNoLessonMonth,
    WorkNotification,
    WorkReport,
    WorkSchoolDeadline,
    WorkSchoolSetting,
)
from app.services.school_deadline_service import enqueue_school_deadline_notices
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession

MONTH = "2026-06"
DEADLINE = date(2026, 6, 25)


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture()
def client():
    return TestClient(app)


def _user(db, email, role, name=None, **kwargs):
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=name or f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=["new"],
        **kwargs,
    )
    db.add(user)
    db.flush()
    return user


def _contract(db, tutor, school):
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name=school.display_name, system_type="new")
    db.add(assignment)
    db.flush()
    profile = WorkAssignmentProfile(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        school_id=school.id,
        form_type="monthly_dispatch",
        is_active=True,
    )
    db.add(profile)
    db.flush()
    return assignment


def _report(db, assignment, tutor, status, month=MONTH):
    report = WorkReport(
        id=uuid.uuid4(),
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        target_month=month,
        form_type="monthly_dispatch",
        form_data={},
        status=status,
    )
    db.add(report)
    db.flush()
    return report


def _setting(db, school, enabled=True, days=3):
    row = WorkSchoolSetting(school_id=school.id, early_check_enabled=enabled, notice_days_before=days)
    db.add(row)
    db.flush()
    return row


def _deadline(db, school, month=MONTH, deadline_date=DEADLINE):
    row = WorkSchoolDeadline(school_id=school.id, target_month=month, deadline_date=deadline_date)
    db.add(row)
    db.flush()
    return row


def _outbox(db):
    return list(db.scalars(select(WorkMailOutbox).order_by(WorkMailOutbox.created_at)))


def _login_headers(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class TestSchoolSettingsApi:
    def test_get_defaults(self, db, client):
        _user(db, "office1@d.example.com", "office")
        school = _user(db, "s1@d.example.com", "school")
        db.commit()
        headers = _login_headers(client, "office1@d.example.com")

        res = client.get(f"/api/w/users/{school.id}/school-settings?year=2026", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["early_check_enabled"] is False
        assert body["notice_days_before"] == 3
        assert body["year"] == 2026
        assert body["deadlines"] == []

    def test_put_saves_settings_and_deadlines(self, db, client):
        _user(db, "office2@d.example.com", "office")
        school = _user(db, "s2@d.example.com", "school")
        db.commit()
        headers = _login_headers(client, "office2@d.example.com")

        payload = {
            "early_check_enabled": True,
            "notice_days_before": 5,
            "year": 2026,
            "deadlines": {"2026-06": "2026-06-25", "2026-07": "2026-08-05"},
        }
        res = client.put(f"/api/w/users/{school.id}/school-settings", json=payload, headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["early_check_enabled"] is True
        assert body["notice_days_before"] == 5
        assert {d["target_month"]: d["deadline_date"] for d in body["deadlines"]} == {
            "2026-06": "2026-06-25",
            "2026-07": "2026-08-05",
        }

        # None（空欄）で削除・他の月は保持
        res = client.put(
            f"/api/w/users/{school.id}/school-settings",
            json={"early_check_enabled": True, "notice_days_before": 5, "year": 2026,
                  "deadlines": {"2026-06": None}},
            headers=headers,
        )
        assert res.status_code == 200
        assert [d["target_month"] for d in res.json()["deadlines"]] == ["2026-07"]

    def test_deadline_change_resets_notice_guard(self, db, client):
        _user(db, "office3@d.example.com", "office")
        school = _user(db, "s3@d.example.com", "school")
        row = _deadline(db, school)
        row.notice_sent_at = datetime.now(timezone.utc)
        db.commit()
        headers = _login_headers(client, "office3@d.example.com")

        res = client.put(
            f"/api/w/users/{school.id}/school-settings",
            json={"early_check_enabled": True, "notice_days_before": 3, "year": 2026,
                  "deadlines": {MONTH: "2026-06-28"}},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        saved = db.scalar(select(WorkSchoolDeadline).where(WorkSchoolDeadline.school_id == school.id))
        db.refresh(saved)
        assert str(saved.deadline_date) == "2026-06-28"
        assert saved.notice_sent_at is None  # 締め日変更＝再送対象へ戻る

    def test_put_same_deadline_keeps_notice_guard(self, db, client):
        _user(db, "office4@d.example.com", "office")
        school = _user(db, "s4@d.example.com", "school")
        row = _deadline(db, school)
        row.notice_sent_at = datetime.now(timezone.utc)
        db.commit()
        headers = _login_headers(client, "office4@d.example.com")

        res = client.put(
            f"/api/w/users/{school.id}/school-settings",
            json={"early_check_enabled": True, "notice_days_before": 3, "year": 2026,
                  "deadlines": {MONTH: str(DEADLINE)}},
            headers=headers,
        )
        assert res.status_code == 200
        saved = db.scalar(select(WorkSchoolDeadline).where(WorkSchoolDeadline.school_id == school.id))
        db.refresh(saved)
        assert saved.notice_sent_at is not None  # 同じ締め日の再保存ではガードを維持する

    def test_rejects_non_school_user(self, db, client):
        _user(db, "office5@d.example.com", "office")
        tutor = _user(db, "t5@d.example.com", "tutor")
        db.commit()
        headers = _login_headers(client, "office5@d.example.com")
        res = client.get(f"/api/w/users/{tutor.id}/school-settings?year=2026", headers=headers)
        assert res.status_code == 409

    def test_requires_staff_role(self, db, client):
        school = _user(db, "s6@d.example.com", "school")
        _user(db, "t6@d.example.com", "tutor")
        db.commit()
        headers = _login_headers(client, "t6@d.example.com")
        res = client.get(f"/api/w/users/{school.id}/school-settings?year=2026", headers=headers)
        assert res.status_code == 403

    def test_validates_month_and_days(self, db, client):
        _user(db, "office7@d.example.com", "office")
        school = _user(db, "s7@d.example.com", "school")
        db.commit()
        headers = _login_headers(client, "office7@d.example.com")

        res = client.put(
            f"/api/w/users/{school.id}/school-settings",
            json={"early_check_enabled": True, "notice_days_before": 3, "year": 2026,
                  "deadlines": {"2026-13": "2026-12-25"}},
            headers=headers,
        )
        assert res.status_code == 422
        res = client.put(
            f"/api/w/users/{school.id}/school-settings",
            json={"early_check_enabled": True, "notice_days_before": 99, "year": 2026, "deadlines": {}},
            headers=headers,
        )
        assert res.status_code == 422


class TestDeadlineNoticeJob:
    def _school_with_pending_report(self, db, *, enabled=True, days=3, skip=False):
        school = _user(db, f"js{uuid.uuid4().hex[:8]}@d.example.com", "school", name="J学校",
                       skip_parent_approval=skip)
        tutor = _user(db, f"jt{uuid.uuid4().hex[:8]}@d.example.com", "tutor", name="未提出講師")
        assignment = _contract(db, tutor, school)
        _report(db, assignment, tutor, WorkStatus.AWAITING_SCHOOL)
        _setting(db, school, enabled=enabled, days=days)
        deadline = _deadline(db, school)
        return school, tutor, deadline

    def test_sends_to_all_sales_within_window(self, db):
        _user(db, "sales1@d.example.com", "sales")
        _user(db, "sales2@d.example.com", "sales")
        _user(db, "office8@d.example.com", "office")  # 事務には送らない（③の宛先は営業全員）
        school, _, deadline = self._school_with_pending_report(db)
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 22)) == 1
        db.commit()

        mails = _outbox(db)
        assert {m.to_email for m in mails} == {"sales1@d.example.com", "sales2@d.example.com"}
        assert all(m.subject.startswith("【至急確認】") for m in mails)
        assert all(f"{MONTH}分" in m.subject and "J学校" in m.subject for m in mails)
        body = mails[0].body
        assert "締め日は 2026年6月25日（木） です" in body
        assert "提出状況を確認してください" in body
        assert "学校承認の状況：承認済み 0/1名" in body
        assert "未提出講師" in body and "学校確認待ち" in body
        db.refresh(deadline)
        assert deadline.notice_sent_at is not None
        notif_types = set(db.scalars(select(WorkNotification.type)))
        assert "school_deadline_notice" in notif_types

    def test_sent_only_once(self, db):
        _user(db, "sales3@d.example.com", "sales")
        self._school_with_pending_report(db)
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 22)) == 1
        db.commit()
        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 23)) == 0
        assert len(_outbox(db)) == 1

    def test_window_boundaries(self, db):
        _user(db, "sales4@d.example.com", "sales")
        _, _, deadline = self._school_with_pending_report(db, days=3)
        db.commit()

        # 窓の前（4日前）と締め日翌日は送らない（遡及送信しない）
        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 21)) == 0
        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 26)) == 0
        db.refresh(deadline)
        assert deadline.notice_sent_at is None
        # 締め日当日は窓内
        assert enqueue_school_deadline_notices(db, today=DEADLINE) == 1

    def test_custom_days_before(self, db):
        _user(db, "sales5@d.example.com", "sales")
        self._school_with_pending_report(db, days=7)
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 18)) == 1  # 7日前から窓に入る

    def test_disabled_early_check_not_sent(self, db):
        _user(db, "sales6@d.example.com", "sales")
        self._school_with_pending_report(db, enabled=False)
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 22)) == 0
        assert _outbox(db) == []

    def test_all_approved_school_skipped_until_incomplete(self, db):
        """全員承認済みの学校には送らない（完了メール①で通知済み）。窓内に未完了へ戻れば送る。"""
        _user(db, "sales7@d.example.com", "sales")
        school = _user(db, "s10@d.example.com", "school")
        tutor = _user(db, "t10@d.example.com", "tutor")
        assignment = _contract(db, tutor, school)
        report = _report(db, assignment, tutor, WorkStatus.AWAITING_OFFICE)  # 承認済み
        _setting(db, school)
        deadline = _deadline(db, school)
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 22)) == 0
        db.refresh(deadline)
        assert deadline.notice_sent_at is None  # ガードは立てない＝未完了へ戻れば翌日以降に送る

        report.status = WorkStatus.RETURNED_TO_TUTOR  # 差戻しで未完了へ
        db.commit()
        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 23)) == 1

    def test_skip_school_sends_plain_notice(self, db):
        """学校確認スキップ校は承認状況の内訳なしで送る（締め日確認自体は行う）。"""
        _user(db, "sales8@d.example.com", "sales")
        self._school_with_pending_report(db, skip=True)
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 22)) == 1
        body = _outbox(db)[0].body
        assert "提出状況を確認してください" in body
        assert "学校承認の状況" not in body

    def test_no_lesson_tutor_noted_as_excluded(self, db):
        """当月授業なし申請中の講師は内訳の対象外として件数を明記する。"""
        _user(db, "sales9@d.example.com", "sales")
        school, tutor, _ = self._school_with_pending_report(db)
        t2 = _user(db, "t11@d.example.com", "tutor", name="休業講師")
        _contract(db, t2, school)
        db.add(WorkNoLessonMonth(tutor_id=t2.id, target_month=MONTH))
        db.commit()

        assert enqueue_school_deadline_notices(db, today=date(2026, 6, 22)) == 1
        body = _outbox(db)[0].body
        assert "承認済み 0/1名" in body
        assert "当月授業なし申請 1名は対象外" in body
