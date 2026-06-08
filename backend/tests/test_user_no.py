from app.core.security import hash_password
from app.models import User
from app.services.user_no_service import (
    ROLE_CATEGORY,
    assign_missing_user_nos,
    band_for_role,
    generate_user_no,
)


def _add(db, email, role, *, tutor_no=None, allowed_systems=("legacy",), user_no=None):
    user = User(
        email=email,
        role=role,
        roles=[role],
        display_name=email,
        tutor_no=tutor_no,
        user_no=user_no,
        allowed_systems=list(allowed_systems),
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(user)
    db.flush()
    return user


def test_band_for_role():
    assert band_for_role("tutor") == 10000
    assert band_for_role("parent") == 20000
    assert band_for_role("admin_receiver") == 30000
    assert band_for_role("admin_reviewer") == 30000
    assert band_for_role("admin_master") == 30000


def test_role_category_grouping():
    assert ROLE_CATEGORY["tutor"] == "講師"
    assert ROLE_CATEGORY["parent"] == "保護者"
    assert ROLE_CATEGORY["admin_receiver"] == "運営スタッフ"
    assert ROLE_CATEGORY["admin_reviewer"] == "運営スタッフ"
    assert ROLE_CATEGORY["admin_master"] == "運営スタッフ"


def test_assign_missing_user_nos_bands_and_scope(db):
    _add(db, "t1@x.com", "tutor")
    _add(db, "t2@x.com", "tutor")
    _add(db, "p1@x.com", "parent")
    _add(db, "r1@x.com", "admin_receiver")
    _add(db, "m1@x.com", "admin_master", allowed_systems=("legacy", "new"))
    _add(db, "school@x.com", "school", allowed_systems=("new",))  # 新システム専用 → 対象外
    db.commit()

    assigned = assign_missing_user_nos(db)
    db.commit()

    by_email = {u.email: u for u in db.query(User).all()}
    assert by_email["t1@x.com"].user_no == "10001"
    assert by_email["t2@x.com"].user_no == "10002"
    assert by_email["p1@x.com"].user_no == "20001"
    assert by_email["r1@x.com"].user_no == "30001"
    assert by_email["m1@x.com"].user_no == "30002"
    assert by_email["school@x.com"].user_no is None
    assert assigned == 5


def test_tutor_no_equals_user_no(db):
    _add(db, "t1@x.com", "tutor")
    db.commit()
    assign_missing_user_nos(db)
    db.commit()
    tutor = db.query(User).filter(User.email == "t1@x.com").one()
    assert tutor.user_no == "10001"
    assert tutor.tutor_no == tutor.user_no  # 講師は user_no と tutor_no を同値に揃える


def test_assign_is_idempotent(db):
    _add(db, "t1@x.com", "tutor")
    db.commit()
    assert assign_missing_user_nos(db) == 1
    db.commit()
    assert assign_missing_user_nos(db) == 0


def test_generate_user_no_continues_band(db):
    _add(db, "p1@x.com", "parent", user_no="20001")
    db.commit()
    assert generate_user_no(db, "parent") == "20002"
    assert generate_user_no(db, "tutor") == "10001"
