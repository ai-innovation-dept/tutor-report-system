"""学校承認の完了通知（EMPS-2026-0709-01 → 改修 202607161140）のテスト。

- 即時通知: 契約講師全員（「当月授業なし」申請中の講師を除く）の学校承認が揃った時点で
  事務・営業の全員へメール。月末+N日の進捗ダイジェストは 202607161140 で廃止。
- 当月授業なし申請: 講師×月の申請（API）で完了判定の対象外になり、申請で全員承認が
  成立した場合はその場で完了メールが飛ぶ。
実メールは送らない（conftest で MAIL_BACKEND=console。投函先の WorkMailOutbox を検証する）。
"""
import asyncio
import uuid
from datetime import date

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
)
from app.services.school_progress_service import (
    school_month_progress,
    send_school_all_approved_after_no_lesson,
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


def _no_lesson(db, tutor, month=MONTH):
    row = WorkNoLessonMonth(tutor_id=tutor.id, target_month=month)
    db.add(row)
    db.flush()
    return row


def _outbox(db):
    return list(db.scalars(select(WorkMailOutbox).order_by(WorkMailOutbox.created_at)))


def _login_headers(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class TestSchoolMonthProgress:
    def test_no_report_counts_as_pending(self, db):
        school = _user(db, "s1@x.example.com", "school", name="A学校")
        t1 = _user(db, "t1@x.example.com", "tutor", name="講師1")
        t2 = _user(db, "t2@x.example.com", "tutor", name="講師2")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 講師2は当月報告書なし＝未作成（未承認扱い）
        _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)

        progress = school_month_progress(db, school, MONTH)
        assert progress is not None
        assert not progress.all_approved
        labels = {e.tutor.email: e.label for e in progress.entries}
        assert labels["t1@x.example.com"] == "承認済み"
        assert labels["t2@x.example.com"] == "未作成"

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

    def test_no_lesson_tutor_excluded_from_target(self, db):
        """「当月授業なし」申請中の講師は集計対象外＝残りの講師全員の承認で完了が成立する。"""
        school = _user(db, "s20@x.example.com", "school")
        t1 = _user(db, "t20a@x.example.com", "tutor", name="承認済講師")
        t2 = _user(db, "t20b@x.example.com", "tutor", name="休業講師")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 報告書なし
        _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        _no_lesson(db, t2)

        progress = school_month_progress(db, school, MONTH)
        assert [e.tutor.email for e in progress.entries] == ["t20a@x.example.com"]
        assert [e.tutor.email for e in progress.no_lesson_entries] == ["t20b@x.example.com"]
        assert progress.all_approved

    def test_no_lesson_only_month_is_not_all_approved(self, db):
        """全講師が授業なし申請の月は完了成立しない（通知対象の実績が無いため）。"""
        school = _user(db, "s21@x.example.com", "school")
        tutor = _user(db, "t21@x.example.com", "tutor")
        _contract(db, tutor, school)
        _no_lesson(db, tutor)

        progress = school_month_progress(db, school, MONTH)
        assert progress is not None
        assert progress.entries == []
        assert not progress.all_approved

    def test_no_lesson_other_month_not_excluded(self, db):
        """申請は月単位＝別の月の集計には影響しない。"""
        school = _user(db, "s22@x.example.com", "school")
        tutor = _user(db, "t22@x.example.com", "tutor")
        _contract(db, tutor, school)
        _no_lesson(db, tutor, month="2026-05")

        progress = school_month_progress(db, school, MONTH)
        assert [e.tutor.email for e in progress.entries] == ["t22@x.example.com"]
        assert not progress.all_approved  # 当月は未作成のまま


class TestImmediateAllApprovedNotification:
    def test_fires_to_office_and_sales(self, db):
        """完了メールは事務・営業の全員へ届く（202607161140で事務を追加）。"""
        _user(db, "sales1@x.example.com", "sales")
        _user(db, "sales2@x.example.com", "sales")
        _user(db, "office1@x.example.com", "office")
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
        assert len(mails) == 3  # 事務1名＋営業2名へ各1通
        assert all("学校承認がすべて完了しました" in m.subject for m in mails)
        assert all("B学校" in m.subject for m in mails)
        assert "契約講師全員（2名）" in mails[0].body
        by_to = {m.to_email: m for m in mails}
        assert "/office/queue" in by_to["office1@x.example.com"].body
        assert "事務担当者 様" in by_to["office1@x.example.com"].body
        assert "/sales/queue" in by_to["sales1@x.example.com"].body
        assert "営業担当者 様" in by_to["sales1@x.example.com"].body
        notif_types = set(db.scalars(select(WorkNotification.type)))
        assert "school_all_approved" in notif_types

    def test_not_fired_while_any_tutor_pending(self, db):
        _user(db, "sales3@x.example.com", "sales")
        _user(db, "office3@x.example.com", "office")
        school = _user(db, "s7@x.example.com", "school")
        t1 = _user(db, "t7a@x.example.com", "tutor")
        t2 = _user(db, "t7b@x.example.com", "tutor")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 報告書なし（未作成）→ 全員承認は成立しない
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

    def test_no_lesson_tutor_listed_as_excluded(self, db):
        """授業なし申請中の講師がいても残り全員の承認で発火し、メールに対象外として明記される。"""
        _user(db, "sales11@x.example.com", "sales")
        school = _user(db, "s23@x.example.com", "school", name="E学校")
        t1 = _user(db, "t23a@x.example.com", "tutor", name="通常講師")
        t2 = _user(db, "t23b@x.example.com", "tutor", name="休業講師")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)
        r1 = _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        _no_lesson(db, t2)
        db.commit()

        asyncio.run(send_school_all_approved_notifications(db, [r1]))

        mails = _outbox(db)
        assert len(mails) == 1
        assert "契約講師全員（1名）" in mails[0].body
        assert "対象外（当月授業なし申請）" in mails[0].body
        assert "休業講師" in mails[0].body

    def test_single_tutor_school_fires_via_api_approval(self, db, client):
        """1講師のみの学校は、その1件の学校承認で即時通知が発火する（API経由の実フロー）。"""
        _user(db, "sales5@x.example.com", "sales")
        _user(db, "office5@x.example.com", "office")
        school = _user(db, "s9@x.example.com", "school", name="C学校")
        tutor = _user(db, "t9@x.example.com", "tutor")
        assignment = _contract(db, tutor, school)
        report = _report(db, assignment, tutor, WorkStatus.AWAITING_SCHOOL)
        report_id = str(report.id)
        db.commit()

        headers = _login_headers(client, "s9@x.example.com")
        res = client.post(f"/api/w/reports/{report_id}/action", json={"action": "approve"}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json()["status"] == WorkStatus.AWAITING_OFFICE

        # 承認時は講師宛の「学校が承認しました」等も投函されるため、完了通知のみに絞って検証する
        completed = [m for m in _outbox(db) if "学校承認がすべて完了しました" in m.subject]
        assert all("C学校" in m.subject for m in completed)
        assert {m.to_email for m in completed} == {"sales5@x.example.com", "office5@x.example.com"}


class TestNoLessonMonthsApi:
    def test_toggle_and_list(self, db, client):
        _user(db, "t30@x.example.com", "tutor")
        db.commit()
        headers = _login_headers(client, "t30@x.example.com")

        res = client.put(f"/api/w/no-lesson-months/{MONTH}", json={"no_lesson": True}, headers=headers)
        assert res.status_code == 200, res.text
        assert res.json() == {"target_month": MONTH, "no_lesson": True}

        res = client.get("/api/w/no-lesson-months", headers=headers)
        assert res.status_code == 200
        assert res.json()["months"] == [MONTH]

        res = client.put(f"/api/w/no-lesson-months/{MONTH}", json={"no_lesson": False}, headers=headers)
        assert res.status_code == 200
        res = client.get("/api/w/no-lesson-months", headers=headers)
        assert res.json()["months"] == []

    def test_requires_tutor_role(self, db, client):
        _user(db, "office30@x.example.com", "office")
        db.commit()
        headers = _login_headers(client, "office30@x.example.com")
        res = client.put(f"/api/w/no-lesson-months/{MONTH}", json={"no_lesson": True}, headers=headers)
        assert res.status_code == 403

    def test_invalid_month_rejected(self, db, client):
        _user(db, "t31@x.example.com", "tutor")
        db.commit()
        headers = _login_headers(client, "t31@x.example.com")
        res = client.put("/api/w/no-lesson-months/2026-13", json={"no_lesson": True}, headers=headers)
        assert res.status_code == 422

    def test_flag_on_completes_school_and_notifies(self, db, client):
        """未達要因だった講師が授業なしを申請すると、その場で完了メールが飛ぶ。"""
        _user(db, "sales31@x.example.com", "sales")
        _user(db, "office31@x.example.com", "office")
        school = _user(db, "s31@x.example.com", "school", name="F学校")
        t1 = _user(db, "t32a@x.example.com", "tutor")
        t2 = _user(db, "t32b@x.example.com", "tutor", name="休業予定講師")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)  # 報告書なし＝未達要因
        _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        db.commit()

        headers = _login_headers(client, "t32b@x.example.com")
        res = client.put(f"/api/w/no-lesson-months/{MONTH}", json={"no_lesson": True}, headers=headers)
        assert res.status_code == 200, res.text

        mails = _outbox(db)
        assert len(mails) == 2  # 事務1名＋営業1名
        assert all("学校承認がすべて完了しました" in m.subject and "F学校" in m.subject for m in mails)
        assert all("休業予定講師" in m.body for m in mails)

    def test_flag_on_when_already_complete_does_not_duplicate(self, db, client):
        """すでに全員承認済み（申請講師も承認済み）の学校へは、申請しても完了メールを再送しない。"""
        _user(db, "sales32@x.example.com", "sales")
        school = _user(db, "s32@x.example.com", "school", name="G学校")
        tutor = _user(db, "t33@x.example.com", "tutor")
        assignment = _contract(db, tutor, school)
        _report(db, assignment, tutor, WorkStatus.AWAITING_OFFICE)  # 承認済み＝完了通知は承認時に送付済み
        db.commit()

        headers = _login_headers(client, "t33@x.example.com")
        res = client.put(f"/api/w/no-lesson-months/{MONTH}", json={"no_lesson": True}, headers=headers)
        assert res.status_code == 200, res.text
        assert _outbox(db) == []

    def test_service_returns_notified_school_count(self, db):
        _user(db, "sales33@x.example.com", "sales")
        school = _user(db, "s33@x.example.com", "school")
        t1 = _user(db, "t34a@x.example.com", "tutor")
        t2 = _user(db, "t34b@x.example.com", "tutor")
        a1 = _contract(db, t1, school)
        _contract(db, t2, school)
        _report(db, a1, t1, WorkStatus.AWAITING_OFFICE)
        _no_lesson(db, t2)
        db.commit()

        assert send_school_all_approved_after_no_lesson(db, t2, MONTH) == 1
