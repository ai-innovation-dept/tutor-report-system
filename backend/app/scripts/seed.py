# === Phase 10: シードデータ START ===
from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import Assignment, User

PASSWORD = "Passw0rd!"


def upsert_user(db, email, role, name, tutor_no=None, allowed_systems=None):
    # admin_master は常に両システム、それ以外は当(legacy)システムのみ。
    if allowed_systems is None:
        allowed_systems = ["legacy", "new"] if role == "admin_master" else ["legacy"]
    user = db.scalar(select(User).where(User.email == email))
    if user:
        user.role = role
        user.roles = [role]
        user.display_name = name
        user.tutor_no = tutor_no
        user.allowed_systems = allowed_systems
        user.password_hash = hash_password(PASSWORD)
        user.is_active = True
        return user

    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=name,
        tutor_no=tutor_no,
        allowed_systems=allowed_systems,
        password_hash=hash_password(PASSWORD),
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def upsert_assignment(db, tutor, parent, student_name):
    assignment = db.scalar(
        select(Assignment).where(
            Assignment.tutor_id == tutor.id,
            Assignment.parent_id == parent.id,
            Assignment.student_name == student_name,
        )
    )
    if assignment:
        assignment.is_active = True
        return assignment

    assignment = Assignment(
        tutor_id=tutor.id,
        parent_id=parent.id,
        student_name=student_name,
        is_active=True,
    )
    db.add(assignment)
    db.flush()
    return assignment


def create_initial_users(db):
    upsert_user(db, "master1@example.com", "admin_master", "管理者")
    upsert_user(db, "receiver1@example.com", "admin_receiver", "受付担当")
    upsert_user(db, "reviewer1@example.com", "admin_reviewer", "再鑑者")
    tutor1 = upsert_user(db, "tutor1@example.com", "tutor", "講師 一郎", "T001")
    tutor2 = upsert_user(db, "tutor2@example.com", "tutor", "講師 二郎", "T002")
    parent1 = upsert_user(db, "parent1@example.com", "parent", "保護者 一郎")
    upsert_assignment(db, tutor1, parent1, "生徒1")
    upsert_assignment(db, tutor2, parent1, "生徒3")


def main():
    db = SessionLocal()
    try:
        create_initial_users(db)
        db.commit()
        print("seed complete")
    finally:
        db.close()


if __name__ == "__main__":
    main()
# === Phase 10 END ===
