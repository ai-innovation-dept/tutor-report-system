"""学校承認の進捗通知（EMPS-2026-0709-01）のテスト。

- 即時通知: 契約講師全員の学校承認が揃った時点で営業へメール
- 締切進捗メール: 月末+N日に未完了の学校の進捗を営業へダイジェスト送信
実メールは送らない（conftest で MAIL_BACKEND=console。投函先の WorkMailOutbox を検証する）。
"""
import asyncio
import calendar
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.main import app
from app.models.shared import Assignment, User
from app.models.work import WorkAssignmentProfile, WorkMailOutbox, WorkNotification, WorkReport
from app.services.school_progress_service import (
    enqueue_monthly_school_progress,
    school_month_progress,
    send_school_all_approved_notifications,
)
from app.workflow.definitions import WorkStatus
from tests.conftest import TestSession

MONTH = "2026-06"


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


def _contract(db, tutor, school, is_active=True, contract_start=None, contract_end=None):
    assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name=school.display_name, system_type="new")
    db.add(assignment)
    db.flush()
    profile = WorkAssignmentProfile(
        assignment_id=assignment.id,
        tutor_id=tutor.id,
        school_id=school.id,
        form_type="monthly_dispatch",
        is_active=is_active,
        contract_start=contract_start,
        contract_end=contract_end,
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


def _outbox(db):
    return list(db.scalars(select(WorkMailOutbox).order_by(WorkMailOutbox.created_at)))


def _deadline_day(month=MONTH):
    year, mon = map(int, month.split("-"))
    last = date(year, mon, calendar.monthrange(year, mon)[1])
    return last + timedelta(days=settings.NEW_SCHOOL_PROGRESS_DAYS_AFTER_MONTH_END)


class TestSchoolMonthProgress:
    def test_no_report_counts_as_no_lessons(self, db):
        school = _user(db, "s1@x.example.com", "school", name="A学校")
        t1 = _user(db, "t1@x.example.com", "tutor", name="講師1")
        t2 = _user(db, "t2@x.example.com", "tutor", name="講師2")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 講師2は当月報告書なし＝当月授業なし
        _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)

        progress = school_month_progress(db, school, MONTH)
        assert progress is not None
        assert not progress.all_approved
        labels = {e.tutor.email: e.label for e in progress.entries}
        assert labels["t1@x.example.com"] == "承認済み"
        assert labels["t2@x.example.com"] == "当月授業なし"

    def test_status_labels(self, db):
        school = _user(db, "s2@x.example.com", "school")
        cases = [
            (WorkStatus.DRAFT, "未提出", False),
            (WorkStatus.AWAITING_SCHOOL, "学校確認待ち", False),
            (WorkStatus.AWAITING_OFFICE_PRECHECK, "事務事前確認中", False),
            (WorkStatus.RETURNED_TO_TUTOR, "差戻し中", False),
            (WorkStatus.AWAITING_OFFICE, "承認済み", True),
            (WorkStatus.AWAITING_SALES, "承認済み", True),
            (WorkStatus.RETURNED_TO_OFFICE, "承認済み", True),
            (WorkStatus.APPROVED, "承認済み", True),
        ]
        tutors = []
        for i, (status, _, _) in enumerate(cases):
            tutor = _user(db, f"tl{i}@x.example.com", "tutor")
            assignment = _contract(db, tutor, school)
            _report(db, assignment, tutor, status)
            tutors.append(tutor)

        progress = school_month_progress(db, school, MONTH)
        by_email = {e.tutor.email: e for e in progress.entries}
        for i, (status, label, approved) in enumerate(cases):
            entry = by_email[f"tl{i}@x.example.com"]
            assert entry.label == label, status
            assert entry.approved is approved, status

    def test_all_approved(self, db):
        school = _user(db, "s3@x.example.com", "school")
        for i in range(2):
            tutor = _user(db, f"ta{i}@x.example.com", "tutor")
            assignment = _contract(db, tutor, school)
            _report(db, assignment, tutor, WorkStatus.AWAITING_OFFICE)
        progress = school_month_progress(db, school, MONTH)
        assert progress.all_approved

    def test_skip_school_returns_none(self, db):
        school = _user(db, "s4@x.example.com", "school", skip_parent_approval=True)
        tutor = _user(db, "t4@x.example.com", "tutor")
        _contract(db, tutor, school)
        assert school_month_progress(db, school, MONTH) is None

    def test_inactive_or_out_of_period_contract_excluded(self, db):
        school = _user(db, "s5@x.example.com", "school")
        t1 = _user(db, "t5a@x.example.com", "tutor")
        t2 = _user(db, "t5b@x.example.com", "tutor")
        t3 = _user(db, "t5c@x.example.com", "tutor")
        _contract(db, t1, school, is_active=False)
        _contract(db, t2, school, contract_end=date(2026, 5, 31))  # 当月前に契約終了
        a3 = _contract(db, t3, school, contract_start=date(2026, 4, 1), contract_end=date(2026, 6, 30))
        _report(db, a3, t3, WorkStatus.AWAITING_OFFICE)

        progress = school_month_progress(db, school, MONTH)
        assert [e.tutor.email for e in progress.entries] == ["t5c@x.example.com"]
        assert progress.all_approved


class TestImmediateAllApprovedNotification:
    def test_fires_when_all_contracted_tutors_approved(self, db):
        _user(db, "sales1@x.example.com", "sales")
        _user(db, "sales2@x.example.com", "sales")
        school = _user(db, "s6@x.example.com", "school", name="B学校")
        t1 = _user(db, "t6a@x.example.com", "tutor")
        t2 = _user(db, "t6b@x.example.com", "tutor")
        a1 = _contract(db, t1, school)
        a2 = _contract(db, t2, school)
        r1 = _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        _report(db, a2, t2, WorkStatus.AWAITING_OFFICE)
        db.commit()

        asyncio.run(send_school_all_approved_notifications(db, [r1]))

        mails = _outbox(db)
        assert len(mails) == 2  # 営業2名へ各1通
        assert all("学校承認がすべて完了しました" in m.subject for m in mails)
        assert all("B学校" in m.subject for m in mails)
        assert "契約講師全員（2名）" in mails[0].body
        notif_types = set(db.scalars(select(WorkNotification.type)))
        assert "school_all_approved" in notif_types

    def test_not_fired_while_any_tutor_pending(self, db):
        _user(db, "sales3@x.example.com", "sales")
        school = _user(db, "s7@x.example.com", "school")
        t1 = _user(db, "t7a@x.example.com", "tutor")
        t2 = _user(db, "t7b@x.example.com", "tutor")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 報告書なし（当月授業なし）→ 全員承認は成立しない
        r1 = _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        db.commit()

        asyncio.run(send_school_all_approved_notifications(db, [r1]))
        assert _outbox(db) == []

    def test_not_fired_for_skip_school(self, db):
        _user(db, "sales4@x.example.com", "sales")
        school = _user(db, "s8@x.example.com", "school", skip_parent_approval=True)
        tutor = _user(db, "t8@x.example.com", "tutor")
        a1 = _contract(db, tutor, school)
        r1 = _report(db, a1, tutor, WorkStatus.AWAITING_OFFICE)
        db.commit()

        asyncio.run(send_school_all_approved_notifications(db, [r1]))
        assert _outbox(db) == []

    def test_single_tutor_school_fires_via_api_approval(self, db, client):
        """1講師のみの学校は、その1件の学校承認で即時通知が発火する（API経由の実フロー）。"""
        _user(db, "sales5@x.example.com", "sales")
        school = _user(db, "s9@x.example.com", "school", name="C学校")
        tutor = _user(db, "t9@x.example.com", "tutor")
        assignment = _contract(db, tutor, school)
        report = _report(db, assignment, tutor, WorkStatus.AWAITING_SCHOOL)
        report_id = str(report.id)
        db.commit()

        res = client.post("/api/auth/login", json={"username": "s9@x.example.com", "password": "Passw0rd!"})
        assert res.status_code == 200, res.text
        headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE

        subjects = [m.subject for m in _outbox(db)]
        assert any("学校承認がすべて完了しました" in s and "C学校" in s for s in subjects)


class TestMonthlyProgressMail:
    def test_sends_digest_on_deadline_day(self, db):
        _user(db, "sales6@x.example.com", "sales")
        school = _user(db, "s10@x.example.com", "school", name="D学校", user_no="40001")
        t1 = _user(db, "t10a@x.example.com", "tutor", name="承認済講師")
        t2 = _user(db, "t10b@x.example.com", "tutor", name="授業なし講師")
        t3 = _user(db, "t10c@x.example.com", "tutor", name="確認待ち講師")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 報告書なし
        a3 = _contract(db, t3, school)
        _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        _report(db, a3, t3, WorkStatus.AWAITING_SCHOOL)
        db.commit()

        count = enqueue_monthly_school_progress(db, today=_deadline_day())
        db.commit()
        assert count == 1

        mails = _outbox(db)
        assert len(mails) == 1
        body = mails[0].body
        assert "D学校" in body
        assert "承認済み 1/3名" in body
        assert "承認済講師" in body
        assert "授業なし講師" in body and "当月授業なし" in body
        assert "確認待ち講師" in body and "学校確認待ち" in body

    def test_not_sent_on_other_days(self, db):
        _user(db, "sales7@x.example.com", "sales")
        school = _user(db, "s11@x.example.com", "school")
        tutor = _user(db, "t11@x.example.com", "tutor")
        _contract(db, tutor, school)
        db.commit()

        assert enqueue_monthly_school_progress(db, today=_deadline_day() + timedelta(days=1)) == 0
        assert enqueue_monthly_school_progress(db, today=_deadline_day() - timedelta(days=1)) == 0
        assert _outbox(db) == []

    def test_all_approved_school_excluded(self, db):
        _user(db, "sales8@x.example.com", "sales")
        school = _user(db, "s12@x.example.com", "school")
        tutor = _user(db, "t12@x.example.com", "tutor")
        a1 = _contract(db, tutor, school)
        _report(db, a1, tutor, WorkStatus.AWAITING_OFFICE)
        db.commit()

        assert enqueue_monthly_school_progress(db, today=_deadline_day()) == 0
        assert _outbox(db) == []

    def test_skip_school_excluded(self, db):
        _user(db, "sales9@x.example.com", "sales")
        school = _user(db, "s13@x.example.com", "school", skip_parent_approval=True)
        tutor = _user(db, "t13@x.example.com", "tutor")
        _contract(db, tutor, school)  # 報告書なし＝未完了だがスキップ校は対象外
        db.commit()

        assert enqueue_monthly_school_progress(db, today=_deadline_day()) == 0
        assert _outbox(db) == []

    def test_sent_only_once_per_month(self, db):
        _user(db, "sales10@x.example.com", "sales")
        school = _user(db, "s14@x.example.com", "school")
        tutor = _user(db, "t14@x.example.com", "tutor")
        _contract(db, tutor, school)  # 報告書なし＝未完了
        db.commit()

        assert enqueue_monthly_school_progress(db, today=_deadline_day()) == 1
        db.commit()
        assert enqueue_monthly_school_progress(db, today=_deadline_day()) == 0
        assert len(_outbox(db)) == 1
