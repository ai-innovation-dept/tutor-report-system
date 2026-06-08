import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["AUTO_CREATE_TABLES"] = "true"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.models import Assignment, User
from app.api import auth, chat, invitations, reports, stale, users, workflow

def override_get_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


for dependency in {get_db, auth.get_db, chat.get_db, invitations.get_db, reports.get_db, stale.get_db, users.get_db, workflow.get_db}:
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


@pytest.fixture()
def client(db):
    tutor = User(email="tutor@example.com", role="tutor", roles=["tutor"], display_name="Tutor", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    parent = User(email="parent@example.com", role="parent", roles=["parent"], display_name="Parent", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    receiver = User(email="receiver@example.com", role="admin_receiver", roles=["admin_receiver"], display_name="Receiver", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    reviewer = User(email="reviewer@example.com", role="admin_reviewer", roles=["admin_reviewer"], display_name="Reviewer", allowed_systems=["legacy"], password_hash=hash_password("Passw0rd!"))
    master = User(email="master@example.com", role="admin_master", roles=["admin_master"], display_name="Master", allowed_systems=["legacy", "new"], password_hash=hash_password("Passw0rd!"))
    db.add_all([tutor, parent, receiver, reviewer, master])
    db.flush()
    db.add(Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name="Student"))
    db.commit()
    db.close()
    return TestClient(app)


def token(client, email):
    res = client.post("/api/auth/login", data={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200
    return res.json()["access_token"]
