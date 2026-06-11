# === Phase 5: 承認ワークフロー START ===
from datetime import date, datetime, time
from uuid import UUID

from app.api import pages as pages_api
from app.api import reports as reports_api
from app.core import time as time_utils
from app.core.security import hash_password
from app.core.time import get_current_jst_date
from app.models import Assignment, LessonReport, Notification, ReportStatus, User
from tests.conftest import token


def test_full_workflow(client, db):
    # 承認フロー: 講師→保護者→受付→再鑑（再鑑承認＝最終承認）。管理者はフロー外。
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    receiver_token = token(client, "receiver@example.com")
    reviewer_token = token(client, "reviewer@example.com")
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()

    report = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
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
        (reviewer_token, "re-review", "admin_approved"),
    ]
    for tk, endpoint, status in steps:
        res = client.post(f"/api/reports/{rid}/{endpoint}", headers={"Authorization": f"Bearer {tk}"}, json={})
        assert res.status_code == 200
        assert res.json()["status"] == status

    # 再鑑承認で再鑑時刻と最終承認時刻の両方が記録される
    db.expire_all()
    final = db.query(LessonReport).filter(LessonReport.id == UUID(rid)).one()
    assert final.re_reviewed_at is not None
    assert final.admin_approved_at is not None

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
    ]
    assert report["events"][-1]["actor_name"] == "Reviewer"
    assert report["events"][-1]["actor_role"] == "admin_reviewer"
    assert report["events"][-1]["created_at"]
    assert "comment" in report["events"][-1]


def test_skip_parent_approval_submits_directly_to_admin(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    parent_user = db.get(User, assignment.parent_id)
    parent_user.skip_parent_approval = True
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
    parent_user = db.get(User, assignment.parent_id)
    parent_user.skip_parent_approval = True
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


def test_parent_with_skip_can_see_admin_approved_reports(client, db):
    # スキップ保護者でも最終承認済み(admin_approved)は閲覧・PDF取得できる。
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    parent_user = db.get(User, assignment.parent_id)
    parent_user.skip_parent_approval = True
    db.add(LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date.today(),
        start_time=time(18, 0),
        end_time=time(19, 0),
        content="final approved",
        target_month=date.today().strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
    ))
    db.commit()

    res = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert [report["status"] for report in res.json()] == [ReportStatus.admin_approved.value]


def test_parent_approve_bulk_auto_submits_to_admin(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    second_assignment = Assignment(tutor_id=assignment.tutor_id, parent_id=assignment.parent_id, student_name="Second Student")
    db.add(second_assignment)
    db.commit()
    today = get_current_jst_date()
    report_ids = []
    for current_assignment, hour in [(assignment, 18), (second_assignment, 19)]:
        res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
            "assignment_id": str(current_assignment.id),
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

    for report_id in report_ids:
        report = db.query(LessonReport).filter(LessonReport.id == UUID(report_id)).one()
        assert report.status == ReportStatus.submitted_to_admin.value
        assert report.parent_approved_at is not None
        assert report.submitted_to_admin_at is not None
    reports = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"}).json()
    assert all(report["id"] not in report_ids for report in reports)


def test_return_requires_comment(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
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
    today = get_current_jst_date()
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


def test_parent_can_return_report(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    created = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    report_id = created.json()["id"]
    client.post(f"/api/reports/{report_id}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})

    returned = client.post(f"/api/reports/{report_id}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "修正してください"})

    assert returned.status_code == 200
    assert returned.json()["status"] == ReportStatus.returned_to_tutor.value


def test_tutor_sees_returned_report(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    created = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    report_id = created.json()["id"]
    client.post(f"/api/reports/{report_id}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})
    returned = client.post("/api/reports/parent-return-bulk", headers={"Authorization": f"Bearer {parent_token}"}, json={
        "report_ids": [report_id],
        "target_month": today.strftime("%Y-%m"),
        "comment": "月次差戻し",
    })
    assert returned.status_code == 200

    reports = client.get("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"})
    by_id = {report["id"]: report for report in reports.json()}
    assert report_id in by_id
    assert by_id[report_id]["status"] == ReportStatus.returned_to_tutor.value
    assert by_id[report_id]["last_return_comment"] == "月次差戻し"


def test_return_report_permission_denied_for_other_parent(client, db):
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    other_parent = User(
        email="other-parent@example.com",
        role="parent",
        roles=["parent"],
        display_name="Other Parent",
        allowed_systems=["legacy"],
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(other_parent)
    db.commit()
    other_parent_token = token(client, "other-parent@example.com")
    today = get_current_jst_date()
    created = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    report_id = created.json()["id"]
    client.post(f"/api/reports/{report_id}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})

    returned = client.post(f"/api/reports/{report_id}/parent-return", headers={"Authorization": f"Bearer {other_parent_token}"}, json={"comment": "不正な差戻し"})

    assert returned.status_code == 403


def test_return_comment_is_saved(client, db):
    tutor_token = token(client, "tutor@example.com")
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    created = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })
    report_id = created.json()["id"]
    client.post(f"/api/reports/{report_id}/submit-to-parent", headers={"Authorization": f"Bearer {tutor_token}"}, json={})

    client.post(f"/api/reports/{report_id}/parent-return", headers={"Authorization": f"Bearer {parent_token}"}, json={"comment": "指導内容を追記してください"})

    report = client.get(f"/api/reports/{report_id}", headers={"Authorization": f"Bearer {tutor_token}"})
    assert report.status_code == 200
    assert report.json()["last_return_comment"] == "指導内容を追記してください"
    assert any(
        event["action"] == "parent_return" and event["comment"] == "指導内容を追記してください"
        for event in report.json()["events"]
    )


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
    second_assignment = Assignment(tutor_id=assignment.tutor_id, parent_id=assignment.parent_id, student_name="Second Student")
    db.add(second_assignment)
    db.commit()
    today = date.today()
    report_ids = []
    for current_assignment, hour in [(assignment, 18), (second_assignment, 19)]:
        res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
            "assignment_id": str(current_assignment.id),
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


def test_list_reports_parent_excludes_submitted_to_admin(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="submitted",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.submitted_to_admin.value,
    )
    db.add(report)
    db.commit()

    res = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert all(item["status"] != ReportStatus.submitted_to_admin.value for item in res.json())


def test_list_reports_parent_excludes_closed(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="closed",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.closed.value,
    )
    db.add(report)
    db.commit()

    res = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert all(item["status"] != ReportStatus.closed.value for item in res.json())


def test_list_reports_parent_includes_awaiting(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="awaiting",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.awaiting_parent_approval.value,
    )
    db.add(report)
    db.commit()

    res = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert any(item["id"] == str(report.id) for item in res.json())


def test_list_reports_parent_includes_admin_approved(client, db):
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="admin approved",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
    )
    db.add(report)
    db.commit()

    res = client.get("/api/reports", headers={"Authorization": f"Bearer {parent_token}"})

    assert res.status_code == 200
    assert any(item["id"] == str(report.id) for item in res.json())


def test_parent_approval_groups_excludes_closed(client, db):
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    db.add_all(
        [
            LessonReport(
                assignment_id=assignment.id,
                tutor_id=assignment.tutor_id,
                parent_id=assignment.parent_id,
                lesson_date=today,
                start_time=time(18, 0),
                end_time=time(19, 0),
                break_minutes=0,
                content="closed",
                target_month=today.strftime("%Y-%m"),
                status=ReportStatus.closed.value,
            ),
            LessonReport(
                assignment_id=assignment.id,
                tutor_id=assignment.tutor_id,
                parent_id=assignment.parent_id,
                lesson_date=today,
                start_time=time(19, 0),
                end_time=time(20, 0),
                break_minutes=0,
                content="awaiting",
                target_month=today.strftime("%Y-%m"),
                status=ReportStatus.awaiting_parent_approval.value,
            ),
        ]
    )
    db.commit()

    groups = pages_api._parent_approval_groups(db, parent)

    assert groups
    assert all(group["current_status"] != ReportStatus.closed.value for group in groups)


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


def test_report_create_allows_current_jst_month_at_utc_month_boundary(client, db, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 6, 1, 8, 30, tzinfo=time_utils.JST)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(time_utils, "datetime", FrozenDateTime)
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()

    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": "2026-06-01",
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })

    assert res.status_code == 200
    assert res.json()["target_month"] == "2026-06"


def test_report_create_rejects_next_jst_month(client, db, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 6, 15, 12, 0, tzinfo=time_utils.JST)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(time_utils, "datetime", FrozenDateTime)
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()

    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": "2026-07-01",
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })

    assert res.status_code == 400
    assert res.json()["detail"] == "当月分の報告書のみ作成できます"


def test_report_create_rejects_previous_jst_month(client, db, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 6, 15, 12, 0, tzinfo=time_utils.JST)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(time_utils, "datetime", FrozenDateTime)
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()

    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": "2026-05-31",
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


def test_create_report_rejects_duplicate_in_progress(client, db):
    """当月分が進行中ステータスの場合も2件目は作成できないこと（409）"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    in_progress = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="in progress",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.awaiting_parent_approval.value,
    )
    db.add(in_progress)
    db.commit()

    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(today),
        "start_time": "19:00",
        "end_time": "20:00",
        "content": "lesson",
    })

    assert res.status_code == 409
    assert res.json()["detail"] == "当月分の報告書がすでに進行中です"


def test_admin_reviewer_can_return_admin_approved_bulk(client, db):
    # 完了（最終承認済み）後の差戻しは最終承認者である再鑑者が受付へ行う
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
        content="approved",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
    )
    db.add(report)
    db.commit()
    res = client.post("/api/reports/admin-return-bulk", headers={"Authorization": f"Bearer {reviewer_token}"}, json={
        "report_ids": [str(report.id)],
        "target_month": today.strftime("%Y-%m"),
        "from_role": "reviewer",
        "comment": "追加修正",
    })
    assert res.status_code == 200
    db.refresh(report)
    assert report.status == ReportStatus.returned_to_receiver.value


def test_admin_master_cannot_act_in_workflow(client, db):
    # 管理者は承認フロー外: from_role=master は廃止(422)、受付・再鑑承認も403
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
    assert res.status_code == 422
    submitted = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(19, 0),
        end_time=time(20, 0),
        break_minutes=0,
        content="submitted",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.submitted_to_admin.value,
    )
    db.add(submitted)
    db.commit()
    receive = client.post(f"/api/reports/{submitted.id}/receive", headers={"Authorization": f"Bearer {master_token}"}, json={})
    assert receive.status_code == 403
    bulk = client.post("/api/reports/admin-receive-bulk", headers={"Authorization": f"Bearer {master_token}"}, json={
        "report_ids": [str(submitted.id)],
        "target_month": today.strftime("%Y-%m"),
    })
    assert bulk.status_code == 403


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


def test_reviewer_return_of_approved_notifies_receiver(client, db, monkeypatch):
    # 完了後の差戻し（再鑑者→受付）でも受付へメール通知される
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
        content="approved",
        target_month=today.strftime("%Y-%m"),
        status=ReportStatus.admin_approved.value,
    )
    db.add(report)
    db.commit()

    returned = client.post(
        "/api/reports/admin-return-bulk",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={
            "report_ids": [str(report.id)],
            "target_month": today.strftime("%Y-%m"),
            "from_role": "reviewer",
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
        allowed_systems=["legacy"],
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


def test_tutor_pdf_receives_approval_stamps(client, db, monkeypatch):
    """講師ロールのPDFには承認印データ（stamps）が渡される（Noneではない）"""
    captured = {}

    def fake_pdf(db, reports, target_month, stamps):
        captured["stamps"] = stamps
        return b"%PDF-1.4\ntutor\n"

    monkeypatch.setattr(reports_api, "_build_reports_pdf", fake_pdf)
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    target_month = today.strftime("%Y-%m")
    db.add(LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="tutor test",
        target_month=target_month,
        status=ReportStatus.admin_approved.value,
    ))
    db.commit()

    res = client.get(
        f"/api/reports/export?target_month={target_month}&format=pdf",
        headers={"Authorization": f"Bearer {tutor_token}"},
    )
    assert res.status_code == 200
    assert captured.get("stamps") is not None, "tutor PDF must receive stamps (not None)"


def test_parent_pdf_receives_no_stamps(client, db, monkeypatch):
    """保護者ロールのPDFには承認印エリアを描画しない（stamps=None）"""
    captured = {}

    def fake_pdf(db, reports, target_month, stamps):
        captured["stamps"] = stamps
        return b"%PDF-1.4\nparent\n"

    monkeypatch.setattr(reports_api, "_build_reports_pdf", fake_pdf)
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    today = date.today()
    target_month = today.strftime("%Y-%m")
    db.add(LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content="parent test",
        target_month=target_month,
        status=ReportStatus.admin_approved.value,
    ))
    db.commit()

    res = client.get(
        f"/api/reports/export?scope=all&target_month={target_month}&format=pdf",
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert res.status_code == 200
    assert captured.get("stamps") is None, "parent PDF must receive stamps=None (no stamp area)"


def test_create_report_rejects_end_time_before_start_time(client, db):
    """終了時刻が開始時刻より早い場合 422 を返す"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(date.today()),
        "start_time": "18:00",
        "end_time": "17:00",
        "subject": "math",
        "content": "lesson",
    })
    assert res.status_code == 422
    detail = res.json()["detail"]
    detail_str = detail if isinstance(detail, str) else str(detail)
    assert "終了時刻" in detail_str


def test_create_report_rejects_equal_start_end_time(client, db):
    """終了時刻と開始時刻が同一の場合 422 を返す"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(date.today()),
        "start_time": "18:00",
        "end_time": "18:00",
        "subject": "math",
        "content": "lesson",
    })
    assert res.status_code == 422
    detail = res.json()["detail"]
    detail_str = detail if isinstance(detail, str) else str(detail)
    assert "終了時刻" in detail_str


def test_create_report_accepts_valid_times(client, db):
    """終了時刻が開始時刻より後の場合は正常作成される"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    res = client.post("/api/reports", headers={"Authorization": f"Bearer {tutor_token}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(date.today()),
        "start_time": "18:00",
        "end_time": "19:00",
        "subject": "math",
        "content": "lesson",
    })
    assert res.status_code == 200


# --- 職務分掌：受付承認と再鑑承認は同一講師で兼務不可 ---

def _make_dual_admin(db, email="dual-admin@example.com", name="Dual Admin"):
    user = User(
        email=email,
        role="admin_receiver",
        roles=["admin_receiver", "admin_reviewer"],
        display_name=name,
        allowed_systems=["legacy"],
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(user)
    db.commit()
    return user


def _make_report(db, assignment, status, content="report"):
    today = date.today()
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=today,
        start_time=time(18, 0),
        end_time=time(19, 0),
        break_minutes=0,
        content=content,
        target_month=today.strftime("%Y-%m"),
        status=status,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def _make_second_tutor_assignment(db, email="tutorE@example.com", name="Tutor E"):
    parent = db.query(User).filter(User.role == "parent").first()
    tutor = User(
        email=email, role="tutor", roles=["tutor"], display_name=name,
        allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"),
    )
    db.add(tutor)
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name=f"{name} Student")
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def test_dual_admin_cannot_re_review_tutor_they_received(client, db):
    """例①：受付・再鑑の両ロールを持つ人が、自分が受付承認した講師を再鑑承認できない。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, ReportStatus.submitted_to_admin.value)

    received = client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert received.status_code == 200
    assert received.json()["status"] == ReportStatus.received.value

    re_reviewed = client.post(f"/api/reports/{report.id}/re-review", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert re_reviewed.status_code == 409
    assert "再鑑承認はできません" in re_reviewed.json()["detail"]


def test_dual_admin_can_receive_other_report_after_re_review(client, db):
    """職務分掌は報告書単位：ある報告書を再鑑承認しても、別の報告書（別生徒・別月）は受付承認できる。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    reviewed = _make_report(db, assignment, ReportStatus.received.value, content="reviewed first")
    res = client.post(f"/api/reports/{reviewed.id}/re-review", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert res.status_code == 200

    # 別の報告書なら受付できる（同一講師でも報告書が違えばロックされない）
    to_receive = _make_report(db, assignment, ReportStatus.submitted_to_admin.value, content="to receive")
    received = client.post(f"/api/reports/{to_receive.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert received.status_code == 200, received.text
    assert received.json()["status"] == ReportStatus.received.value


def test_dual_admin_can_re_review_other_tutor_received_by_someone_else(client, db):
    """例④：自分が受付承認したのは講師B。講師Eは他者が受付承認済みなら再鑑承認できる。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    receiver_token = token(client, "receiver@example.com")
    tutor_b = db.query(Assignment).first()
    # 講師Bを受付承認
    report_b = _make_report(db, tutor_b, ReportStatus.submitted_to_admin.value, content="tutorB")
    assert client.post(f"/api/reports/{report_b.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={}).status_code == 200

    # 講師Eは別の受付担当が受付承認
    tutor_e = _make_second_tutor_assignment(db)
    report_e = _make_report(db, tutor_e, ReportStatus.submitted_to_admin.value, content="tutorE")
    assert client.post(f"/api/reports/{report_e.id}/receive", headers={"Authorization": f"Bearer {receiver_token}"}, json={}).status_code == 200

    # dual は講師Eを再鑑承認できる（再鑑承認＝最終承認）
    re_reviewed = client.post(f"/api/reports/{report_e.id}/re-review", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert re_reviewed.status_code == 200
    assert re_reviewed.json()["status"] == ReportStatus.admin_approved.value


def test_other_dual_admin_can_re_review_tutor_received_by_first(client, db):
    """例②：別人(C)は、Aが受付承認した講師Bを再鑑承認できる。"""
    _make_dual_admin(db, email="dualA@example.com", name="Dual A")
    _make_dual_admin(db, email="dualC@example.com", name="Dual C")
    token_a = token(client, "dualA@example.com")
    token_c = token(client, "dualC@example.com")
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, ReportStatus.submitted_to_admin.value)
    assert client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {token_a}"}, json={}).status_code == 200
    re_reviewed = client.post(f"/api/reports/{report.id}/re-review", headers={"Authorization": f"Bearer {token_c}"}, json={})
    assert re_reviewed.status_code == 200


def test_admin_master_is_out_of_workflow(client, db):
    """admin_master は承認フロー外のため、受付・再鑑のいずれの承認も実行できない。"""
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, ReportStatus.submitted_to_admin.value)
    assert client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {master_token}"}, json={}).status_code == 403
    report2 = _make_report(db, assignment, ReportStatus.received.value, content="received report")
    assert client.post(f"/api/reports/{report2.id}/re-review", headers={"Authorization": f"Bearer {master_token}"}, json={}).status_code == 403


def test_separation_locks_endpoint_reports_acted_reports(client, db):
    """UI制御用エンドポイントが受付/再鑑済みの報告書IDを返す。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, ReportStatus.submitted_to_admin.value)
    client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={})

    res = client.get("/api/reports/admin-separation-locks", headers={"Authorization": f"Bearer {dual_token}"})
    assert res.status_code == 200
    data = res.json()
    assert str(report.id) in data["received_report_ids"]
    assert data["reviewed_report_ids"] == []


def test_dual_admin_cannot_return_from_reviewer_report_they_received(client, db):
    """同一報告書を受付承認した人は、その報告書の再鑑「差戻し」も承認と同様に禁止。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    # 報告書を受付承認（→ status: received）
    report = _make_report(db, assignment, ReportStatus.submitted_to_admin.value)
    assert client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={}).status_code == 200
    # 同じ報告書を再鑑差戻ししようとするとブロック
    blocked = client.post(
        f"/api/reports/{report.id}/return-from-reviewer",
        headers={"Authorization": f"Bearer {dual_token}"},
        json={"comment": "要修正"},
    )
    assert blocked.status_code == 409, blocked.text
    assert "再鑑差戻しはできません" in blocked.json()["detail"]


def test_dual_admin_can_return_from_receiver_other_report_after_re_review(client, db):
    """職務分掌は報告書単位：ある報告書を再鑑承認しても、別の報告書は受付差戻しできる。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    reviewed = _make_report(db, assignment, ReportStatus.received.value)
    assert client.post(f"/api/reports/{reviewed.id}/re-review", headers={"Authorization": f"Bearer {dual_token}"}, json={}).status_code == 200
    # 別の報告書なら受付差戻し可能
    to_receive = _make_report(db, assignment, ReportStatus.submitted_to_admin.value, content="to receive")
    res = client.post(
        f"/api/reports/{to_receive.id}/return-from-receiver",
        headers={"Authorization": f"Bearer {dual_token}"},
        json={"comment": "要修正"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == ReportStatus.returned_to_tutor.value


def test_dual_admin_first_return_from_reviewer_allowed(client, db):
    """まだ何も対応していない講師なら、再鑑差戻しは可能（最初の対応は許可）。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, ReportStatus.received.value)
    res = client.post(
        f"/api/reports/{report.id}/return-from-reviewer",
        headers={"Authorization": f"Bearer {dual_token}"},
        json={"comment": "要修正"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == ReportStatus.returned_to_receiver.value


def test_receiver_can_return_after_full_cycle_and_reviewer_return(client, db):
    """事象確認2：C受付→D再鑑（最終承認）→D完了後差戻し後、受付者Cは同じ報告書を受付差戻しできる
    （Cは再鑑していないため職務分掌に抵触しない）。"""
    _make_dual_admin(db, email="cAdmin@example.com", name="C Admin")
    _make_dual_admin(db, email="dAdmin@example.com", name="D Admin")
    c_token = token(client, "cAdmin@example.com")
    d_token = token(client, "dAdmin@example.com")
    assignment = db.query(Assignment).first()
    report = _make_report(db, assignment, ReportStatus.submitted_to_admin.value)

    assert client.post(f"/api/reports/{report.id}/receive", headers={"Authorization": f"Bearer {c_token}"}, json={}).status_code == 200
    # 再鑑承認＝最終承認
    approved = client.post(f"/api/reports/{report.id}/re-review", headers={"Authorization": f"Bearer {d_token}"}, json={})
    assert approved.status_code == 200
    assert approved.json()["status"] == ReportStatus.admin_approved.value
    # 完了後の差戻しは再鑑者が受付へ行う
    assert client.post(f"/api/reports/{report.id}/return-from-reviewer", headers={"Authorization": f"Bearer {d_token}"}, json={"comment": "再確認"}).status_code == 200

    # 受付者Cは（再鑑していないので）同じ報告書を受付差戻しできる
    res = client.post(f"/api/reports/{report.id}/return-from-receiver", headers={"Authorization": f"Bearer {c_token}"}, json={"comment": "修正依頼"})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == ReportStatus.returned_to_tutor.value


def test_receiver_blocked_only_on_same_report_not_other_student(client, db):
    """事象確認1：B報告書を受付した人は、同じ講師でも別報告書（別生徒相当）を受付承認できる。
    一方で、自分が受付した報告書の再鑑承認は引き続き不可。"""
    _make_dual_admin(db)
    dual_token = token(client, "dual-admin@example.com")
    assignment = db.query(Assignment).first()
    report_b = _make_report(db, assignment, ReportStatus.submitted_to_admin.value, content="student B")
    assert client.post(f"/api/reports/{report_b.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={}).status_code == 200

    # 同じ報告書(B)の再鑑承認は不可（兼務禁止が効く）
    blocked = client.post(f"/api/reports/{report_b.id}/re-review", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert blocked.status_code == 409
    assert "再鑑承認はできません" in blocked.json()["detail"]

    # 別報告書(別生徒相当)は受付承認できる
    report_c = _make_report(db, assignment, ReportStatus.submitted_to_admin.value, content="student C")
    received = client.post(f"/api/reports/{report_c.id}/receive", headers={"Authorization": f"Bearer {dual_token}"}, json={})
    assert received.status_code == 200, received.text
    assert received.json()["status"] == ReportStatus.received.value
# === Phase 5 END ===
