# === Phase 10: シードデータ START ===
from sqlalchemy import select

from app.core.security import hash_password
from app.database import SessionLocal
from app.models import User

PASSWORD = "Passw0rd!"


def upsert_user(db, email, role, name, tutor_no=None):
    user = db.scalar(select(User).where(User.email == email))
    if user:
        user.role = role
        user.display_name = name
        user.tutor_no = tutor_no
        user.password_hash = hash_password(PASSWORD)
        user.is_active = True
        return user

    user = User(
        email=email,
        role=role,
        display_name=name,
        tutor_no=tutor_no,
        password_hash=hash_password(PASSWORD),
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def create_initial_users(db):
    upsert_user(db, "master1@example.com", "admin_master", "管理者")
    upsert_user(db, "receiver1@example.com", "admin_receiver", "受付担当")
    upsert_user(db, "reviewer1@example.com", "admin_reviewer", "再鑑者")
    upsert_user(db, "tutor1@example.com", "tutor", "講師 一郎", "T001")
    upsert_user(db, "tutor2@example.com", "tutor", "講師 二郎", "T002")


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
