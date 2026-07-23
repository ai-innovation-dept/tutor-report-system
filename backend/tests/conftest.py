import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["AUTO_CREATE_TABLES"] = "true"
# テストでは実メールを一切送らない（ログ出力のみ）。送信キューのドレイナも smtp 時のみ起動する。
os.environ["MAIL_BACKEND"] = "console"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.time import get_current_jst_month
from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.models import Assignment, MonthlyReport, User
from app.api import auth, chat, invitations, monthly_reports, parent_surveys, reports, stale, users, workflow

def override_get_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


for dependency in {get_db, auth.get_db, chat.get_db, invitations.get_db, monthly_reports.get_db, parent_surveys.get_db, reports.get_db, stale.get_db, users.get_db, workflow.get_db}:
    app.dependency_overrides[dependency] = override_get_db


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def seed_monthly_report(db, assignment, target_month: str | None = None, parent_note: str | None = "（テスト）よろしくお願いします。"):
    """作成済み（承認依頼の必須項目入力済み・保護者記入欄も記入済み）の指導月報を用意する。

    承認依頼は指導月報（問題点と対策。学年ほか他項目は任意＝改修 202607231755 ④）の作成が、
    保護者承認は保護者記入欄の入力が必須のため、月報機能自体を対象としない既存フローのテストは
    このヘルパーで前提を満たす。同一 担当×対象月 は1件（unique制約）のため冪等。
    """
    month = target_month or get_current_jst_month()
    existing = (
        db.query(MonthlyReport)
        .filter(MonthlyReport.assignment_id == assignment.id, MonthlyReport.target_month == month)
        .first()
    )
    if existing:
        return existing
    monthly = MonthlyReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        target_month=month,
        grade="小5",
        form_data={"issues": ["計算ミスを減らす", "", "", "", ""]},
        parent_note=parent_note,
        parent_note_by=assignment.parent_id if parent_note else None,
        parent_note_at=datetime.now(timezone.utc) if parent_note else None,
    )
    db.add(monthly)
    db.commit()
    return monthly


@pytest.fixture()
def client(db):
    tutor = User(email="tutor@example.com", role="tutor", roles=["tutor"], display_name="Tutor", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    parent = User(email="parent@example.com", role="parent", roles=["parent"], display_name="Parent", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    receiver = User(email="receiver@example.com", role="admin_receiver", roles=["admin_receiver"], display_name="Receiver", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    reviewer = User(email="reviewer@example.com", role="admin_reviewer", roles=["admin_reviewer"], display_name="Reviewer", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    master = User(email="master@example.com", role="admin_master", roles=["admin_master"], display_name="Master", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, parent, receiver, reviewer, master])
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name="Student")
    db.add(assignment)
    db.flush()
    # 当月分の指導月報（記入済み）。承認依頼・保護者承認の月報ガードで既存テストが止まらないようにする。
    seed_monthly_report(db, assignment)
    db.commit()
    db.close()
    return TestClient(app)


def token(client, email):
    res = client.post("/api/auth/login", data={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200
    return res.json()["access_token"]
