from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import Assignment, LessonReport, ReportEvent, ReportStatus, User


PASSWORD = "Passw0rd!"


@pytest.fixture(autouse=True)
def scenario_pdf_stub(monkeypatch):
    monkeypatch.setattr("app.api.reports._build_reports_pdf", lambda db, reports, target_month, stamps: b"%PDF scenario")


@pytest.fixture()
def scenario_db():
    """シナリオテスト専用DB。テスト終了後に自動リセット。"""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def scenario_client(scenario_db):
    """
    2講師2保護者3生徒受付再鑑管理者を作成する。
    """
    users = {
        "tutor_a": User(email="tutor_a@example.com", role="tutor", roles=["tutor"], display_name="大橋悟史", password_hash=hash_password(PASSWORD)),
        "tutor_b": User(email="tutor_b@example.com", role="tutor", roles=["tutor"], display_name="鈴木二郎", password_hash=hash_password(PASSWORD)),
        "parent_a": User(email="parent_a@example.com", role="parent", roles=["parent"], display_name="保護者ア", password_hash=hash_password(PASSWORD)),
        "parent_b": User(email="parent_b@example.com", role="parent", roles=["parent"], display_name="保護者イ", password_hash=hash_password(PASSWORD)),
        "receiver": User(email="receiver@scenario.com", role="admin_receiver", roles=["admin_receiver"], display_name="受付", password_hash=hash_password(PASSWORD)),
        "reviewer": User(email="reviewer@scenario.com", role="admin_reviewer", roles=["admin_reviewer"], display_name="再鑑", password_hash=hash_password(PASSWORD)),
        "master": User(email="master@scenario.com", role="admin_master", roles=["admin_master"], display_name="管理者", password_hash=hash_password(PASSWORD)),
    }
    scenario_db.add_all(users.values())
    scenario_db.flush()
    assignments = {
        "student1": Assignment(tutor_id=users["tutor_a"].id, parent_id=users["parent_a"].id, student_name="生徒1"),
        "student2": Assignment(tutor_id=users["tutor_a"].id, parent_id=users["parent_a"].id, student_name="生徒2"),
        "student3": Assignment(tutor_id=users["tutor_b"].id, parent_id=users["parent_b"].id, student_name="生徒3"),
    }
    scenario_db.add_all(assignments.values())
    scenario_db.commit()

    client = TestClient(app)
    tokens = {key: login(client, user.email) for key, user in users.items()}
    return {"client": client, "tokens": tokens, "users": users, "assignments": assignments, "db": scenario_db}


class FrozenDate:
    """指定月を「当月」として扱う"""

    def __init__(self, year: int, month: int):
        self.year = year
        self.month = month

    def patch(self, monkeypatch):
        target_month = f"{self.year}-{self.month:02d}"
        frozen_now = datetime(self.year, self.month, 15, 9, 0, tzinfo=timezone(timedelta(hours=9)))
        monkeypatch.setattr("app.core.time.get_current_jst_month", lambda: target_month)
        monkeypatch.setattr("app.api.reports._current_month", lambda: target_month)
        monkeypatch.setattr("app.core.time.get_current_jst", lambda: frozen_now)
        monkeypatch.setattr("app.services.report_service.get_current_jst", lambda: frozen_now)


def login(client: TestClient, email: str) -> str:
    response = client.post("/api/auth/login", data={"username": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def report_by_id(db, report_id: str) -> LessonReport:
    db.expire_all()
    return db.get(LessonReport, UUID(report_id))


def assert_ok(response, expected: int = 200):
    assert response.status_code == expected, response.text
    return response


def post_action(client: TestClient, token: str, report_id: str, action: str, payload: dict | None = None):
    return assert_ok(client.post(f"/api/reports/{report_id}/{action}", headers=auth(token), json=payload or {}))


def post_bulk(client: TestClient, token: str, endpoint: str, report_ids: list[str], target_month: str, payload: dict | None = None):
    body = {"report_ids": report_ids, "target_month": target_month}
    if payload:
        body.update(payload)
    return assert_ok(client.post(endpoint, headers=auth(token), json=body))


def create_report(client, token, assignment_id, lesson_date, start="18:00", end="19:00", subject="数学", content="指導内容"):
    """報告書を1件作成して report_id を返す"""
    response = assert_ok(client.post(
        "/api/reports",
        headers=auth(token),
        json={
            "assignment_id": str(assignment_id),
            "lesson_date": str(lesson_date),
            "start_time": start,
            "end_time": end,
            "subject": subject,
            "content": content,
        },
    ))
    return response.json()["id"]


def seed_report(db, assignment: Assignment, lesson_date: date, index: int, status: str = ReportStatus.draft.value) -> str:
    start_hour = 8 + index
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=lesson_date,
        start_time=time(start_hour, 0),
        end_time=time(start_hour + 1, 0),
        break_minutes=0,
        subject="数学",
        content=f"指導内容 {index}",
        target_month=lesson_date.strftime("%Y-%m"),
        status=status,
    )
    db.add(report)
    db.flush()
    db.add(ReportEvent(report_id=report.id, actor_id=assignment.tutor_id, action="create", to_status=status))
    db.commit()
    return str(report.id)


def create_month_reports(client, tokens, assignment_id, year, month, count=10):
    """指定月に count 件の報告書を作成して report_id リストを返す"""
    db = tokens["db"]
    assignment = db.get(Assignment, assignment_id)
    return [seed_report(db, assignment, date(year, month, day), day) for day in range(1, count + 1)]


def full_approve(client, tokens, report_ids, with_return=None):
    """
    最終承認まで実行する。
    with_return: None / parent / receiver / reviewer / master
    """
    db = tokens["db"]
    reports = [report_by_id(db, report_id) for report_id in report_ids]
    target_month = reports[0].target_month
    tutor_token = tokens["tutor_by_id"][reports[0].tutor_id]
    parent_token = tokens["parent_by_id"][reports[0].parent_id]

    post_bulk(client, tutor_token, "/api/reports/submit-to-parent-bulk", report_ids, target_month)

    if with_return == "parent":
        post_bulk(client, parent_token, "/api/reports/parent-return-bulk", report_ids, target_month, {"comment": "保護者差戻しコメント"})
        assert all(report_by_id(db, report_id).status == ReportStatus.returned_to_tutor.value for report_id in report_ids)
        post_bulk(client, tutor_token, "/api/reports/submit-to-parent-bulk", report_ids, target_month)

    post_bulk(client, parent_token, "/api/reports/parent-approve-bulk", report_ids, target_month)

    if with_return == "receiver":
        assert_ok(client.post(
            "/api/reports/admin-return-bulk",
            headers=auth(tokens["receiver"]),
            json={"report_ids": report_ids, "target_month": target_month, "from_role": "receiver", "comment": "受付差戻しコメント"},
        ))
        post_bulk(client, tutor_token, "/api/reports/submit-to-parent-bulk", report_ids, target_month)
        post_bulk(client, parent_token, "/api/reports/parent-approve-bulk", report_ids, target_month)

    post_bulk(client, tokens["receiver"], "/api/reports/admin-receive-bulk", report_ids, target_month)

    if with_return == "reviewer":
        assert_ok(client.post(
            "/api/reports/admin-return-bulk",
            headers=auth(tokens["reviewer"]),
            json={"report_ids": report_ids, "target_month": target_month, "from_role": "reviewer", "comment": "再鑑差戻しコメント"},
        ))
        post_bulk(client, tokens["receiver"], "/api/reports/admin-receive-bulk", report_ids, target_month)

    post_bulk(client, tokens["reviewer"], "/api/reports/admin-review-bulk", report_ids, target_month)

    if with_return == "master":
        assert_ok(client.post(
            "/api/reports/admin-return-bulk",
            headers=auth(tokens["master"]),
            json={"report_ids": report_ids, "target_month": target_month, "from_role": "master", "comment": "管理者差戻しコメント"},
        ))
        post_bulk(client, tokens["receiver"], "/api/reports/admin-receive-bulk", report_ids, target_month)
        post_bulk(client, tokens["reviewer"], "/api/reports/admin-review-bulk", report_ids, target_month)

    post_bulk(client, tokens["master"], "/api/reports/admin-approve-bulk", report_ids, target_month)
    return [report_by_id(db, report_id) for report_id in report_ids]


@pytest.fixture()
def scenario(scenario_client):
    data = scenario_client
    data["tokens"]["db"] = data["db"]
    data["tokens"]["tutor_by_id"] = {
        data["users"]["tutor_a"].id: data["tokens"]["tutor_a"],
        data["users"]["tutor_b"].id: data["tokens"]["tutor_b"],
    }
    data["tokens"]["parent_by_id"] = {
        data["users"]["parent_a"].id: data["tokens"]["parent_a"],
        data["users"]["parent_b"].id: data["tokens"]["parent_b"],
    }
    return data


def test_scenario_full_approve_all_months(scenario, scenario_db, monkeypatch):
    """3ヶ月3生徒各10件を差戻しなしで最終承認まで完了させる。"""
    client, tokens, assignments = scenario["client"], scenario["tokens"], scenario["assignments"]
    all_ids = []
    for year, month in [(2026, 4), (2026, 5), (2026, 6)]:
        FrozenDate(year, month).patch(monkeypatch)
        for assignment in assignments.values():
            ids = create_month_reports(client, tokens, assignment.id, year, month, count=10)
            all_ids.extend(ids)
            full_approve(client, tokens, ids)

    reports = scenario_db.scalars(select(LessonReport)).all()
    assert len(reports) == 90
    assert all(report.status == ReportStatus.admin_approved.value for report in reports)
    for report in reports:
        actions = [event.action for event in scenario_db.scalars(select(ReportEvent).where(ReportEvent.report_id == report.id)).all()]
        assert actions == ["create", "submit_to_parent", "parent_approve", "submit_to_admin", "receive", "re_review", "admin_approve"]

    parent_reports = client.get("/api/reports", headers=auth(tokens["parent_a"]))
    assert parent_reports.status_code == 200
    assert any(item["status"] == ReportStatus.admin_approved.value for item in parent_reports.json())


def test_scenario_parent_return_and_resubmit(scenario, scenario_db, monkeypatch):
    """保護者が差戻し 講師が修正 再提出 最終承認。"""
    FrozenDate(2026, 6).patch(monkeypatch)
    client, tokens, assignment = scenario["client"], scenario["tokens"], scenario["assignments"]["student1"]
    ids = create_month_reports(client, tokens, assignment.id, 2026, 6, count=3)
    post_bulk(client, tokens["tutor_a"], "/api/reports/submit-to-parent-bulk", ids, "2026-06")
    post_bulk(client, tokens["parent_a"], "/api/reports/parent-return-bulk", ids, "2026-06", {"comment": "時間が合いません"})
    assert all(report_by_id(scenario_db, report_id).status == ReportStatus.returned_to_tutor.value for report_id in ids)

    report = report_by_id(scenario_db, ids[0])
    report.content = "修正済み"
    scenario_db.commit()
    approved = full_approve(client, tokens, ids)
    assert all(report.status == ReportStatus.admin_approved.value for report in approved)
    events = client.get(f"/api/reports/{ids[0]}", headers=auth(tokens["parent_a"])).json()["events"]
    assert any(event["action"] == "parent_return" and event["comment"] == "時間が合いません" for event in events)


def test_scenario_receiver_return(scenario, scenario_db, monkeypatch):
    """受付が差戻し 講師が修正 再提出 最終承認"""
    FrozenDate(2026, 6).patch(monkeypatch)
    ids = create_month_reports(scenario["client"], scenario["tokens"], scenario["assignments"]["student1"].id, 2026, 6, count=2)
    approved = full_approve(scenario["client"], scenario["tokens"], ids, with_return="receiver")
    assert all(report.status == ReportStatus.admin_approved.value for report in approved)
    assert any(event.action == "return_from_receiver" for event in scenario_db.scalars(select(ReportEvent).where(ReportEvent.report_id == UUID(ids[0]))))


def test_scenario_reviewer_return(scenario, scenario_db, monkeypatch):
    """再鑑が差戻し 受付が再受付 再鑑が再鑑 最終承認"""
    FrozenDate(2026, 6).patch(monkeypatch)
    ids = create_month_reports(scenario["client"], scenario["tokens"], scenario["assignments"]["student1"].id, 2026, 6, count=2)
    approved = full_approve(scenario["client"], scenario["tokens"], ids, with_return="reviewer")
    assert all(report.status == ReportStatus.admin_approved.value for report in approved)
    assert any(event.action == "return_from_reviewer" for event in scenario_db.scalars(select(ReportEvent).where(ReportEvent.report_id == UUID(ids[0]))))


def test_scenario_master_return(scenario, scenario_db, monkeypatch):
    """管理者が差戻し 受付が再受付 最終承認"""
    FrozenDate(2026, 6).patch(monkeypatch)
    ids = create_month_reports(scenario["client"], scenario["tokens"], scenario["assignments"]["student1"].id, 2026, 6, count=2)
    approved = full_approve(scenario["client"], scenario["tokens"], ids, with_return="master")
    assert all(report.status == ReportStatus.admin_approved.value for report in approved)
    assert any(event.action == "return_from_master" for event in scenario_db.scalars(select(ReportEvent).where(ReportEvent.report_id == UUID(ids[0]))))


def test_scenario_duplicate_creation_blocked(scenario, scenario_db, monkeypatch):
    """当月に報告書が存在する状態で追加作成を試みると 409 になること。"""
    FrozenDate(2026, 6).patch(monkeypatch)
    client, token_value, assignment = scenario["client"], scenario["tokens"]["tutor_a"], scenario["assignments"]["student1"]
    # draft / returned_to_tutor は保護者未提出のため重複チェック対象外 → 201
    for status in [
        ReportStatus.draft.value,
        ReportStatus.returned_to_tutor.value,
    ]:
        scenario_db.query(ReportEvent).delete()
        scenario_db.query(LessonReport).delete()
        scenario_db.commit()
        seed_report(scenario_db, assignment, date(2026, 6, 1), 1, status=status)
        response = client.post("/api/reports", headers=auth(token_value), json={
            "assignment_id": str(assignment.id),
            "lesson_date": "2026-06-02",
            "start_time": "18:00",
            "end_time": "19:00",
            "content": "重複",
        })
        assert response.status_code == 200

    # 保護者提出済み以降のステータスは重複不可 → 409
    for status in [
        ReportStatus.awaiting_parent_approval.value,
        ReportStatus.admin_approved.value,
    ]:
        scenario_db.query(ReportEvent).delete()
        scenario_db.query(LessonReport).delete()
        scenario_db.commit()
        seed_report(scenario_db, assignment, date(2026, 6, 1), 1, status=status)
        response = client.post("/api/reports", headers=auth(token_value), json={
            "assignment_id": str(assignment.id),
            "lesson_date": "2026-06-02",
            "start_time": "18:00",
            "end_time": "19:00",
            "content": "重複",
        })
        assert response.status_code == 409

    scenario_db.query(ReportEvent).delete()
    scenario_db.query(LessonReport).delete()
    scenario_db.commit()
    seed_report(scenario_db, assignment, date(2026, 6, 1), 1, status=ReportStatus.closed.value)
    created = create_report(client, token_value, assignment.id, "2026-06-02")
    assert report_by_id(scenario_db, created).status == ReportStatus.draft.value


def test_scenario_parent_list_shows_correct_statuses(scenario, scenario_db, monkeypatch):
    """保護者の list_reports が正しいステータスのみ返すこと。"""
    FrozenDate(2026, 6).patch(monkeypatch)
    client, tokens, assignment = scenario["client"], scenario["tokens"], scenario["assignments"]["student1"]
    statuses = [
        ReportStatus.draft.value,
        ReportStatus.awaiting_parent_approval.value,
        ReportStatus.returned_to_tutor.value,
        ReportStatus.parent_approved.value,
        ReportStatus.submitted_to_admin.value,
        ReportStatus.received.value,
        ReportStatus.re_reviewed.value,
        ReportStatus.returned_to_receiver.value,
        ReportStatus.admin_approved.value,
        ReportStatus.closed.value,
    ]
    ids_by_status = {
        status: seed_report(scenario_db, assignment, date(2026, 6, index + 1), index + 1, status=status)
        for index, status in enumerate(statuses)
    }
    response = client.get("/api/reports", headers=auth(tokens["parent_a"]))
    returned_statuses = {item["status"] for item in response.json()}
    assert {ReportStatus.awaiting_parent_approval.value, ReportStatus.returned_to_tutor.value, ReportStatus.parent_approved.value, ReportStatus.admin_approved.value}.issubset(returned_statuses)
    assert ReportStatus.draft.value not in returned_statuses
    assert ReportStatus.submitted_to_admin.value not in returned_statuses
    assert ReportStatus.received.value not in returned_statuses
    assert ReportStatus.re_reviewed.value not in returned_statuses
    assert ReportStatus.returned_to_receiver.value not in returned_statuses
    assert ReportStatus.closed.value not in returned_statuses
    assert ids_by_status[ReportStatus.closed.value] not in {item["id"] for item in response.json()}


def test_scenario_parent_operation_history(scenario, scenario_db, monkeypatch):
    """保護者の操作履歴が正しく記録されること。"""
    FrozenDate(2026, 6).patch(monkeypatch)
    client, tokens, assignment = scenario["client"], scenario["tokens"], scenario["assignments"]["student1"]
    approved_id = seed_report(scenario_db, assignment, date(2026, 6, 1), 1)
    returned_id = seed_report(scenario_db, assignment, date(2026, 6, 2), 2)
    untouched_id = seed_report(scenario_db, assignment, date(2026, 6, 3), 3)
    post_action(client, tokens["tutor_a"], approved_id, "submit-to-parent")
    post_action(client, tokens["parent_a"], approved_id, "parent-approve")
    post_action(client, tokens["tutor_a"], returned_id, "submit-to-parent")
    post_action(client, tokens["parent_a"], returned_id, "parent-return", {"comment": "コメントあり"})

    approved_events = client.get(f"/api/reports/{approved_id}", headers=auth(tokens["parent_a"])).json()["events"]
    returned_events = client.get(f"/api/reports/{returned_id}", headers=auth(tokens["parent_a"])).json()["events"]
    untouched_events = client.get(f"/api/reports/{untouched_id}", headers=auth(tokens["tutor_a"])).json()["events"]
    assert any(event["action"] == "parent_approve" and event["created_at"] for event in approved_events)
    assert any(event["action"] == "parent_return" and event["comment"] == "コメントあり" and event["created_at"] for event in returned_events)
    assert not any(event["action"] in {"parent_approve", "parent_return"} for event in untouched_events)


def test_scenario_stale_detection(scenario, scenario_db, monkeypatch):
    """先月以前の未処理報告書が stale として検知されること。"""
    FrozenDate(2026, 6).patch(monkeypatch)
    client, tokens, assignments = scenario["client"], scenario["tokens"], scenario["assignments"]
    stale_a = seed_report(scenario_db, assignments["student1"], date(2026, 5, 1), 1, status=ReportStatus.returned_to_tutor.value)
    stale_b = seed_report(scenario_db, assignments["student3"], date(2026, 5, 2), 2, status=ReportStatus.awaiting_parent_approval.value)
    seed_report(scenario_db, assignments["student2"], date(2026, 5, 3), 3, status=ReportStatus.closed.value)

    assert client.get("/api/stale-count", headers=auth(tokens["tutor_a"])).json()["count"] == 1
    assert client.get("/api/stale-count", headers=auth(tokens["tutor_b"])).json()["count"] == 1
    assert client.get("/api/stale-count", headers=auth(tokens["parent_a"])).json()["count"] == 1
    assert client.get("/api/stale-count", headers=auth(tokens["parent_b"])).json()["count"] == 1
    assert client.get("/api/stale-count", headers=auth(tokens["receiver"])).json()["count"] == 2

    closed = client.post(f"/api/reports/{stale_a}/close", headers=auth(tokens["master"]), json={"close_reason": "対応不要"})
    assert closed.status_code == 200
    assert client.get("/api/stale-count", headers=auth(tokens["receiver"])).json()["count"] == 1
    assert report_by_id(scenario_db, stale_b).status == ReportStatus.awaiting_parent_approval.value


def test_scenario_close_report(scenario, scenario_db, monkeypatch):
    """管理者がクローズした報告書が各画面から除外されること。"""
    FrozenDate(2026, 6).patch(monkeypatch)
    client, tokens, assignment = scenario["client"], scenario["tokens"], scenario["assignments"]["student1"]
    report_id = seed_report(scenario_db, assignment, date(2026, 5, 1), 1, status=ReportStatus.returned_to_tutor.value)
    closed = client.post(f"/api/reports/{report_id}/close", headers=auth(tokens["master"]), json={"close_reason": "重複報告書のため無効化"})
    assert closed.status_code == 200

    report = report_by_id(scenario_db, report_id)
    assert report.status == ReportStatus.closed.value
    assert report.close_reason == "重複報告書のため無効化"
    assert report.closed_at is not None
    assert report.closed_by == scenario["users"]["master"].id
    assert report_id not in {item["id"] for item in client.get("/api/reports", headers=auth(tokens["parent_a"])).json()}
    assert report_id in {item["id"] for item in client.get("/api/reports", headers=auth(tokens["tutor_a"])).json()}
    assert client.get("/api/stale-count", headers=auth(tokens["master"])).json()["count"] == 0

    created = create_report(client, tokens["tutor_a"], assignment.id, "2026-06-01")
    assert report_by_id(scenario_db, created).status == ReportStatus.draft.value


def test_scenario_realistic_three_months(scenario, scenario_db, monkeypatch):
    """4月5月6月に渡る実際の運用を再現する。"""
    client, tokens, assignments = scenario["client"], scenario["tokens"], scenario["assignments"]

    FrozenDate(2026, 4).patch(monkeypatch)
    april_1 = create_month_reports(client, tokens, assignments["student1"].id, 2026, 4, 10)
    april_2 = create_month_reports(client, tokens, assignments["student2"].id, 2026, 4, 10)
    april_3 = create_month_reports(client, tokens, assignments["student3"].id, 2026, 4, 10)
    full_approve(client, tokens, april_1, with_return="parent")
    full_approve(client, tokens, april_2)
    full_approve(client, tokens, april_3, with_return="receiver")

    FrozenDate(2026, 5).patch(monkeypatch)
    may_1 = create_month_reports(client, tokens, assignments["student1"].id, 2026, 5, 10)
    may_2 = create_month_reports(client, tokens, assignments["student2"].id, 2026, 5, 10)
    may_3 = create_month_reports(client, tokens, assignments["student3"].id, 2026, 5, 10)
    full_approve(client, tokens, may_1)
    full_approve(client, tokens, may_2, with_return="reviewer")
    full_approve(client, tokens, may_3, with_return="master")

    FrozenDate(2026, 7).patch(monkeypatch)
    june_1 = create_month_reports(client, tokens, assignments["student1"].id, 2026, 6, 10)
    june_2 = create_month_reports(client, tokens, assignments["student2"].id, 2026, 6, 10)
    june_3 = create_month_reports(client, tokens, assignments["student3"].id, 2026, 6, 10)
    post_bulk(client, tokens["tutor_a"], "/api/reports/submit-to-parent-bulk", june_1, "2026-06")
    post_bulk(client, tokens["tutor_a"], "/api/reports/submit-to-parent-bulk", june_2, "2026-06")
    post_bulk(client, tokens["parent_a"], "/api/reports/parent-return-bulk", june_2, "2026-06", {"comment": "6月差戻し"})
    post_bulk(client, tokens["tutor_b"], "/api/reports/submit-to-parent-bulk", june_3, "2026-06")
    post_bulk(client, tokens["parent_b"], "/api/reports/parent-approve-bulk", june_3, "2026-06")
    post_bulk(client, tokens["receiver"], "/api/reports/admin-receive-bulk", june_3, "2026-06")

    approved = scenario_db.scalars(select(LessonReport).where(LessonReport.target_month.in_(["2026-04", "2026-05"]))).all()
    assert len(approved) == 60
    assert all(report.status == ReportStatus.admin_approved.value for report in approved)
    assert client.get("/api/stale-count", headers=auth(tokens["receiver"])).json()["count"] == 30

    parent_a_reports = client.get("/api/reports", headers=auth(tokens["parent_a"])).json()
    assert any(item["status"] == ReportStatus.admin_approved.value and item["target_month"] == "2026-04" for item in parent_a_reports)
    assert any(event["action"] == "parent_return" and event["comment"] == "6月差戻し" for item in parent_a_reports for event in item["events"])

    FrozenDate(2026, 6).patch(monkeypatch)
    duplicate = client.post("/api/reports", headers=auth(tokens["tutor_a"]), json={
        "assignment_id": str(assignments["student1"].id),
        "lesson_date": "2026-06-20",
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "重複",
    })
    assert duplicate.status_code == 409

    exported = client.get("/api/reports/export?target_month=2026-05&format=pdf&scope=all", headers=auth(tokens["parent_a"]))
    assert exported.status_code == 200
    blocked_export = client.get("/api/reports/export?target_month=2026-06&format=pdf&scope=all", headers=auth(tokens["parent_a"]))
    assert blocked_export.status_code == 404
