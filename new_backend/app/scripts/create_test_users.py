from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.shared import User


PASSWORD = "Passw0rd!"

TEST_USERS = [
    {
        "email": "school1@example.com",
        "display_name": "椅子戸学園",
        "role": "school",
        "roles": ["school"],
        "allowed_systems": ["new"],
    },
    {
        "email": "sales1@example.com",
        "display_name": "営業担当一郎",
        "role": "sales",
        "roles": ["sales"],
        "allowed_systems": ["new"],
    },
    {
        "email": "office1@example.com",
        "display_name": "事務担当一郎",
        "role": "office",
        "roles": ["office"],
        "allowed_systems": ["new"],
    },
]


def upsert_test_user(db, spec: dict) -> str:
    user = db.scalar(select(User).where(User.email == spec["email"]))
    if user is None:
        user = User(
            email=spec["email"],
            password_hash=hash_password(PASSWORD),
            display_name=spec["display_name"],
            role=spec["role"],
            roles=spec["roles"],
            allowed_systems=spec["allowed_systems"],
            is_active=True,
        )
        db.add(user)
        return "created"

    user.password_hash = hash_password(PASSWORD)
    user.display_name = spec["display_name"]
    user.role = spec["role"]
    user.roles = spec["roles"]
    user.allowed_systems = spec["allowed_systems"]
    user.is_active = True
    user.deleted_at = None
    return "updated"


def main() -> None:
    db = SessionLocal()
    try:
        results = []
        for spec in TEST_USERS:
            action = upsert_test_user(db, spec)
            results.append(f"{action}: {spec['email']}")
        db.commit()
    finally:
        db.close()

    for result in results:
        print(result)


if __name__ == "__main__":
    main()
