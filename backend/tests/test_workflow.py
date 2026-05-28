# === Phase 5: 承認ワークフロー START ===
from datetime import date, time
from uuid import UUID

from app.api import reports as reports_api
from app.core.security import hash_password
from app.models import Assignment, LessonReport, Notification, ReportStatus, User
from tests.conftest import token


def test_full_workflow(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    receiver_token = token(client, "receiver@example.com")
    reviewer_token = token(client, "reviewer@example.com")
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()

    report = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(date(2026, 5, 1)),
        "start_time": "18:00",
        "end_time": "19:00",
        "subject": "math",
        "content": "lesson",
    })
    assert report.status_code == 200
    rid = report.json()["id"]
    steps = [
        (tutor_token, "submit-to-parent", "awaiting_parent_approval"),
        (parent_token, "parent-approve", "submitted_to_admin"),
        (receiver_token, "receive", "received"),
        (reviewer_token, "re-review", "re_reviewed"),
        (master_token, "admin-approve", "admin_approved"),
    ]
    for tk, endpoint, status in steps:
        res = client.post(f"/api/reports/{rid}/{endpoint}", headers={"Authorization": f"Bearer {tk}"}, json={})
        assert res.status_code == 200
        assert res.json()["status"] == status

    listed = client.get("/api/reports", headers={"Authorization": f"Bearer {master_token}"})
    assert listed.status_code == 200
    report = next(item for item in listed.json() if item["id"] == rid)
    assert [event["action"] for event in report["events"]] == [
        "create",
        "submit_to_parent",
        "parent_approve",
        "submit_to_admin",
        "receive",
        "re_review",
        "admin_approve",
    ]
    assert report["events"][-1]["actor_name"] == "Master"
    assert report["events"][-1]["actor_role"] == "admin_master"
    assert report["events"][-1]["created_at"]
    assert "comment" in report["events"][-1]


def test_skip_parent_approval_submits_directly_to_admin(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    assignment.skip_parent_approval = True
    db.commit()

    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": date.today().isoformat(),
        "start_time": "18:00",
        "end_time": "19:00",
        "break_minutes": 0,
        "content": "skip parent",
    })
    assert res.status_code == 200
    rid = res.json()["id"]

    submitted = client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})

    assert submitted.status_code == 200
    assert submitted.json()["status"] == ReportStatus.submitted_to_admin.value
    report = db.query(LessonReport).filter(LessonReport.id == UUID(rid)).one()
    assert report.submitted_to_admin_at is not None
    assert report.submitted_to_parent_at is None
    parent_notifications = db.query(Notification).filter(Notification.user_id == assignment.parent_id).all()
    assert parent_notifications == []
    receiver = db.query(User).filter(User.role == "admin_receiver").one()
    receiver_notifications = db.query(Notification).filter(Notification.user_id == receiver.id).all()
    assert len(receiver_notifications) == 1


def test_parent_report_list_hides_skip_parent_approval_reports(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    assignment.skip_parent_approval = True
    db.add(LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date.today(),
        start_time=time(18, 0),
        end_time=time(19, 0),
        content="hidden from parent",
        target_month=date.today().strftime("%Y-%m"),
        status=ReportStatus.submitted_to_admin.value,
    ))
    db.commit()

    res = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert res.json() == []


def test_parent_approve_bulk_auto_submits_to_admin(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report_ids = []
    for hour in [18, 19]:
        res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
            "assignment_id": str(assignment.id),
            "lesson_date": str(today),
            "start_time": f"{hour:02d}:00",
            "end_time": f"{hour + 1:02d}:00",
            "content": f"lesson {hour}",
        })
        report_ids.append(res.json()["id"])
    client.post("/api/reports/submit-to-parent-bulk", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "report_ids": report_ids,
        "target_month": today.strftime("%Y-%m"),
    })

    approved = client.post("/api/reports/parent-approve-bulk", headers={"Authorization": f"Bearer {parent_token}"}, json={
        "report_ids": report_ids,
        "target_month": today.strftime("%Y-%m"),
    })
    assert approved.status_code == 200

    reports = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"}).json()
    by_id = {report["id"]: report for report in reports}
    for report_id in report_ids:
        assert by_id[report_id]["status"] == ReportStatus.submitted_to_admin.value
        assert by_id[report_id]["parent_approved_at"] is not None
        assert by_id[report_id]["submitted_to_admin_at"] is not None


def test_return_requires_comment(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": "2026-05-01",
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    rid = res.json()["id"]
    client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    returned = client.post(f"/api/reports/{rid}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={})
    assert returned.status_code == 422


def test_returned_report_can_be_resubmitted_to_parent(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    rid = res.json()["id"]
    client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    returned = client.post(f"/api/reports/{rid}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "修正してください"})
    assert returned.status_code == 200
    assert returned.json()["status"] == ReportStatus.returned_to_tutor.value

    resubmitted = client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    assert resubmitted.status_code == 200
    assert resubmitted.json()["status"] == ReportStatus.awaiting_parent_approval.value


def test_parent_return_requires_non_blank_comment(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    rid = res.json()["id"]
    client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})

    returned = client.post(f"/api/reports/{rid}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "   "})
    assert returned.status_code == 422


def test_parent_can_cancel_individual_return(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    rid = res.json()["id"]
    client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    client.post(f"/api/reports/{rid}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "この日だけ修正"})

    canceled = client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {parent_token}"}, json={})
    assert canceled.status_code == 200
    assert canceled.json()["status"] == ReportStatus.awaiting_parent_approval.value


def test_returned_reports_can_be_resubmitted_to_parent_bulk(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    target_month = today.strftime("%Y-%m")
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    rid = res.json()["id"]
    client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    client.post(f"/api/reports/{rid}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "修正してください"})

    resubmitted = client.post("/api/reports/submit-to-parent-bulk", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "report_ids": [rid],
        "target_month": target_month,
    })
    assert resubmitted.status_code == 200
    reports = client.get("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}).json()
    by_id = {report["id"]: report for report in reports}
    assert by_id[rid]["status"] == ReportStatus.awaiting_parent_approval.value


def test_return_comment_is_limited_to_returned_report(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report_ids = []
    for hour in [18, 19]:
        res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
            "assignment_id": str(assignment.id),
            "lesson_date": str(today),
            "start_time": f"{hour:02d}:00",
            "end_time": f"{hour + 1:02d}:00",
            "content": f"lesson {hour}",
        })
        assert res.status_code == 200
        report_ids.append(res.json()["id"])

    returned_id = report_ids[0]
    client.post(f"/api/reports/{returned_id}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    client.post(f"/api/reports/{returned_id}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "5月6日のみ修正"})

    reports = client.get("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}).json()
    by_id = {report["id"]: report for report in reports}
    assert by_id[returned_id]["last_return_comment"] == "5月6日のみ修正"
    assert by_id[returned_id]["status"] == ReportStatus.returned_to_tutor.value
    assert by_id[report_ids[1]]["last_return_comment"] is None


def test_parent_reports_can_filter_by_tutor(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    first_tutor = db.get(User, assignment.tutor_id)
    first_tutor.display_name = "講師 一郎"
    second_tutor = User(
        email="tutor2@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="講師 二郎",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(second_tutor)
    db.flush()
    second_assignment = Assignment(tutor_id=second_tutor.id, parent_id=assignment.parent_id, student_name="Student 2")
    db.add(second_assignment)
    db.flush()
    target_month = date.today().strftime("%Y-%m")
    reports = [
        LessonReport(
            assignment_id=assignment.id,
            tutor_id=assignment.tutor_id,
            parent_id=assignment.parent_id,
            lesson_date=date.today(),
            start_time=time(18, 0),
            end_time=time(19, 0),
            break_minutes=0,
            content="first tutor",
            target_month=target_month,
            status=ReportStatus.awaiting_parent_approval.value,
        ),
        LessonReport(
            assignment_id=second_assignment.id,
            tutor_id=second_tutor.id,
            parent_id=assignment.parent_id,
            lesson_date=date.today(),
            start_time=time(19, 0),
            end_time=time(20, 0),
            break_minutes=0,
            content="second tutor",
            target_month=target_month,
            status=ReportStatus.awaiting_parent_approval.value,
        ),
    ]
    db.add_all(reports)
    db.commit()

    all_reports = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})
    assert all_reports.status_code == 200
    assert {report["tutor_name"] for report in all_reports.json()} == {"講師 一郎", "講師 二郎"}

    filtered = client.get(f"/api/reports?tutor_id={second_tutor.id}", headers={"Authorization": f"Bearer {parent_token}"})
    assert filtered.status_code == 200
    assert [report["content"] for report in filtered.json()] == ["second tutor"]
    assert filtered.json()[0]["tutor_name"] == "講師 二郎"


def test_tutor_reports_page_shows_return_comment_badge(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    rid = res.json()["id"]
    client.post(f"/api/reports/{rid}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    client.post(f"/api/reports/{rid}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "この日だけ修正"})

    token(client, "tutor@example.com")
    page = client.get("/tutor/reports")
    assert page.status_code == 200
    assert "差戻しあり" in page.text
    assert "差戻しあり 1件" not in page.text
    assert "returnCommentsList" in page.text
    assert "last_return_comment" in page.text
    assert "差戻し理由:" not in page.text


def test_report_create_rejects_non_current_month(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    non_current = date(date.today().year - 1, 1, 1)
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(non_current),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    assert res.status_code == 400
    assert res.json()["detail"] == "当月分の報告書のみ作成できます"


def test_report_create_rejects_after_admin_approved(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    approved = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="approved",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
    )
    db.add(approved)
    db.commit()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "19:00",
        "end_time": "20:00",
        "content": "lesson",
    })
    assert res.status_code == 409
    assert "最終承認済み" in res.json()["detail"]


def test_admin_master_can_return_admin_approved_bulk(client, db):
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="approved",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
    )
    db.add(report)
    db.commit()
    res = client.post("/api/reports/admin-return-bulk", headers={"Authorization": f"Bearer {master_token}"}, json={
        "report_ids": [str(report.id)],
        "target_month": today.strftime("%Y-%m"),
        "from_role": "master",
        "comment": "追加修正",
    })
    assert res.status_code == 200
    db.refresh(report)
    assert report.status == ReportStatus.returned_to_receiver.value


def test_admin_reviewer_return_goes_to_receiver_and_can_be_received(client, db):
    receiver_token = token(client, "receiver@example.com")
    reviewer_token = token(client, "reviewer@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="received",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.re_reviewed.value,
    )
    db.add(report)
    db.commit()

    returned = client.post(f"/api/reports/{report.id}/return-from-reviewer", headers={"Authorization": f"Bearer {reviewer_token}"}, json={"comment": "受付で確認"})
    assert returned.status_code == 200
    assert returned.json()["status"] == ReportStatus.returned_to_receiver.value

    received = client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {receiver_token}"}, json={})
    assert received.status_code == 200
    assert received.json()["status"] == ReportStatus.received.value


def test_admin_receiver_can_receive_submitted_to_admin_bulk(client, db):
    receiver_token = token(client, "receiver@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="submitted to admin",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.submitted_to_admin.value,
    )
    db.add(report)
    db.commit()

    received = client.post(
        "/api/reports/admin-receive-bulk",
        headers={"Authorization": f"Bearer {receiver_token}"},
        json={"report_ids": [str(report.id)], "target_month": today.strftime("%Y-%m")},
    )

    assert received.status_code == 200
    db.refresh(report)
    assert report.status == ReportStatus.received.value


def test_admin_receiver_can_receive_returned_to_receiver_bulk(client, db):
    receiver_token = token(client, "receiver@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="returned to receiver",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.returned_to_receiver.value,
    )
    db.add(report)
    db.commit()

    received = client.post(
        "/api/reports/admin-receive-bulk",
        headers={"Authorization": f"Bearer {receiver_token}"},
        json={"report_ids": [str(report.id)], "target_month": today.strftime("%Y-%m")},
    )

    assert received.status_code == 200
    db.refresh(report)
    assert report.status == ReportStatus.received.value


def test_return_from_reviewer_notifies_receiver(client, db, monkeypatch):
    sent = []

    async def fake_send(to_email, subject, template_name, context):
        sent.append((to_email, subject, template_name, context))

    monkeypatch.setattr("app.services.workflow_service.send_email_notification", fake_send)
    reviewer_token = token(client, "reviewer@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="received",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.received.value,
    )
    db.add(report)
    db.commit()

    returned = client.post(
        f"/api/reports/{report.id}/return-from-reviewer",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={"comment": "受付で確認してください"},
    )

    assert returned.status_code == 200
    assert [call[0] for call in sent] == ["receiver@example.com"]


def test_return_from_master_notifies_receiver(client, db, monkeypatch):
    sent = []

    async def fake_send(to_email, subject, template_name, context):
        sent.append((to_email, subject, template_name, context))

    monkeypatch.setattr("app.services.workflow_service.send_email_notification", fake_send)
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="re reviewed",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.re_reviewed.value,
    )
    db.add(report)
    db.commit()

    returned = client.post(
        "/api/reports/admin-return-bulk",
        headers={"Authorization": f"Bearer {master_token}"},
        json={
            "report_ids": [str(report.id)],
            "target_month": today.strftime("%Y-%m"),
            "from_role": "master",
            "comment": "受付で確認してください",
        },
    )

    assert returned.status_code == 200
    assert [call[0] for call in sent] == ["receiver@example.com"]


def test_selected_role_cookie_is_respected(client, db):
    multi_role_user = User(
        email="multi-admin@example.com",
        role="admin_receiver",
        roles=["admin_receiver", "admin_reviewer"],
        display_name="Multi Admin",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(multi_role_user)
    db.commit()
    access_token = token(client, "multi-admin@example.com")
    client.cookies.set("selected_role", "admin_reviewer")

    reports = client.get("/api/reports", headers={"Authorization": f"Bearer {access_token}"})
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {access_token}"})

    assert reports.status_code == 200
    assert me.status_code == 200
    assert me.json()["role"] == "admin_reviewer"


def test_admin_can_export_all_reports_as_pdf(client, db, monkeypatch):
    exported = {}

    def fake_pdf(db, reports, target_month, stamps):
        exported["reports"] = reports
        exported["stamps"] = stamps
        return b"%PDF-1.4\nadmin\n"

    monkeypatch.setattr(reports_api, "_build_reports_pdf", fake_pdf)
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    tutor = db.get(User, assignment.tutor_id)
    parent = db.get(User, assignment.parent_id)
    second_assignment = Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name="Student 2")
    db.add(second_assignment)
    db.flush()
    today = date.today()
    target_month = today.strftime("%Y-%m")
    db.add_all([
        LessonReport(
            assignment_id=assignment.id,
            tutor_id=tutor.id,
            parent_id=parent.id,
            lesson_date=today,
            start_time=time(18, 0),
            end_time=time(19, 30),
            break_minutes=10,
            subject="math",
            content="first student",
            target_month=target_month,
            status=ReportStatus.admin_approved.value,
        ),
        LessonReport(
            assignment_id=second_assignment.id,
            tutor_id=tutor.id,
            parent_id=parent.id,
            lesson_date=today,
            start_time=time(20, 0),
            end_time=time(21, 0),
            break_minutes=0,
            subject="english",
            content="second student",
            target_month=target_month,
            status=ReportStatus.admin_approved.value,
        ),
        LessonReport(
            assignment_id=second_assignment.id,
            tutor_id=tutor.id,
            parent_id=parent.id,
            lesson_date=today,
            start_time=time(21, 0),
            end_time=time(22, 0),
            break_minutes=0,
            subject="science",
            content="not approved",
            target_month=target_month,
            status=ReportStatus.received.value,
        ),
    ])
    db.commit()

    res = client.get(
        f"/api/reports/export?scope=approved_only&target_month={target_month}&format=pdf",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert ".pdf" in res.headers["content-disposition"]
    assert exported["reports"]
    assert all(report.status == ReportStatus.admin_approved.value for report in exported["reports"])


def test_parent_can_export_all_children_as_pdf(client, db, monkeypatch):
    monkeypatch.setattr(reports_api, "_build_reports_pdf", lambda db, reports, target_month, stamps: b"%PDF-1.4\nparent\n")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    tutor = db.get(User, assignment.tutor_id)
    parent = db.get(User, assignment.parent_id)
    second_assignment = Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name="Student 2")
    db.add(second_assignment)
    db.flush()
    today = date.today()
    target_month = today.strftime("%Y-%m")
    db.add_all([
        LessonReport(
            assignment_id=assignment.id,
            tutor_id=tutor.id,
            parent_id=parent.id,
            lesson_date=today,
            start_time=time(18, 0),
            end_time=time(19, 0),
            break_minutes=0,
            content="first child",
            target_month=target_month,
            status=ReportStatus.admin_approved.value,
        ),
        LessonReport(
            assignment_id=second_assignment.id,
            tutor_id=tutor.id,
            parent_id=parent.id,
            lesson_date=today,
            start_time=time(19, 0),
            end_time=time(20, 0),
            break_minutes=5,
            content="second child",
            target_month=target_month,
            status=ReportStatus.admin_approved.value,
        ),
    ])
    db.commit()

    res = client.get(
        f"/api/reports/export?scope=all&target_month={target_month}&format=pdf",
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")


def test_export_rejects_non_pdf_format(client, db):
    parent_token = token(client, "parent@example.com")
    res = client.get(
        f"/api/reports/export?scope=all&target_month={date.today().strftime('%Y-%m')}&format=csv",
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert res.status_code == 422
    assert res.json()["detail"] == "format must be pdf"


def test_tutor_cannot_export_other_tutor_reports(client, db):
    tutor_token = token(client, "tutor@example.com")
    second_tutor = User(
        email="tutor2@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="Tutor 2",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(second_tutor)
    db.commit()

    res = client.get(
        f"/api/reports/export?tutor_id={second_tutor.id}&target_month={date.today().strftime('%Y-%m')}&format=pdf",
        headers={"Authorization": f"Bearer {tutor_token}"},
    )
    assert res.status_code == 403
# === Phase 5 END ===
