import uuid

from sqlalchemy import select

from app.models.shared import Assignment, User
from app.models.work import WorkNotification, WorkReport, WorkReportEvent
from app.services.workflow_service import execute_transition
from app.workflow.definitions import WorkAction, WorkStatus
from tests.conftest import TestSession


def _user(role: str, email: str) -> User:
    return User(
        email=email,
        role=role,
        roles=[role],
        display_name=role,
        password_hash="hash",
    )


def test_execute_transition_adds_event_once_and_enqueues_school_notification():
    db = TestSession()
    try:
        tutor = _user("tutor", "tutor.workflow@example.com")
        school = _user("school", "school.workflow@example.com")
        db.add_all([tutor, school])
        db.flush()

        assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="生徒A")
        db.add(assignment)
        db.flush()

        report = WorkReport(
            id=uuid.uuid4(),
            assignment_id=assignment.id,
            tutor_id=tutor.id,
            target_month="2026-06",
            form_type="monthly_dispatch",
            form_data={},
            status=WorkStatus.DRAFT,
            current_approver_role="tutor",
        )
        db.add(report)
        db.flush()

        execute_transition(db, report, tutor, "tutor", WorkAction.SUBMIT, None)

        events = list(db.scalars(select(WorkReportEvent).where(WorkReportEvent.report_id == report.id)))
        notifications = list(db.scalars(select(WorkNotification).where(WorkNotification.report_id == report.id)))

        assert report.status == WorkStatus.AWAITING_SCHOOL
        assert len(events) == 1
        assert events[0].from_status == WorkStatus.DRAFT
        assert events[0].to_status == WorkStatus.AWAITING_SCHOOL
        assert len(notifications) == 1
        assert notifications[0].user_id == school.id
        assert notifications[0].channel == "email"
        assert notifications[0].type == "approval_request"
        assert notifications[0].subject == "【業務連絡表】承認依頼が届きました"
        assert notifications[0].sent_at is None
    finally:
        db.rollback()
        db.close()


def test_final_approval_enqueues_tutor_and_school_notifications():
    # 営業承認で最終承認（経理ステップ廃止）。講師・学校へ完了通知
    db = TestSession()
    try:
        tutor = _user("tutor", "tutor.final@example.com")
        school = _user("school", "school.final@example.com")
        sales = _user("sales", "sales.final@example.com")
        db.add_all([tutor, school, sales])
        db.flush()

        assignment = Assignment(tutor_id=tutor.id, parent_id=school.id, student_name="生徒B")
        db.add(assignment)
        db.flush()

        report = WorkReport(
            id=uuid.uuid4(),
            assignment_id=assignment.id,
            tutor_id=tutor.id,
            target_month="2026-06",
            form_type="monthly_dispatch",
            form_data={},
            status=WorkStatus.AWAITING_SALES,
            current_approver_role="sales",
        )
        db.add(report)
        db.flush()

        execute_transition(db, report, sales, "sales", WorkAction.APPROVE, None)

        notifications = list(db.scalars(select(WorkNotification).where(WorkNotification.report_id == report.id)))

        assert report.status == WorkStatus.APPROVED
        assert {notification.user_id for notification in notifications} == {tutor.id, school.id}
        assert {notification.type for notification in notifications} == {"final_approved"}
        assert {notification.sent_at for notification in notifications} == {None}
    finally:
        db.rollback()
        db.close()
