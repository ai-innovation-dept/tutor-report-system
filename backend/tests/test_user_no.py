from app.core.security import hash_password
from app.models import User
from app.services.user_no_service import (
    ROLE_CATEGORY,
    assign_missing_user_nos,
    band_for_role,
    derive_user_no_from_tutor_no,
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


def test_derive_user_no_from_tutor_no():
    assert derive_user_no_from_tutor_no("T001") == "T1001"
    assert derive_user_no_from_tutor_no("T003") == "T1003"
    assert derive_user_no_from_tutor_no("T1003") == "T1003"  # 冪等
    assert derive_user_no_from_tutor_no(None) is None
    assert derive_user_no_from_tutor_no("X9") is None


def test_band_for_role():
    assert band_for_role("tutor") == 1000
    assert band_for_role("parent") == 2000
    assert band_for_role("admin_receiver") == 3000
    assert band_for_role("admin_reviewer") == 3000
    assert band_for_role("admin_master") == 3000


def test_role_category_grouping():
    assert ROLE_CATEGORY["tutor"] == "講師"
    assert ROLE_CATEGORY["parent"] == "保護者"
    assert ROLE_CATEGORY["admin_receiver"] == "運営スタッフ"
    assert ROLE_CATEGORY["admin_reviewer"] == "運営スタッフ"
    assert ROLE_CATEGORY["admin_master"] == "運営スタッフ"


def test_assign_missing_user_nos_bands_and_scope(db):
    _add(db, "t1@x.com", "tutor", tutor_no="T001")
    _add(db, "t2@x.com", "tutor", tutor_no="T002")
    _add(db, "p1@x.com", "parent")
    _add(db, "r1@x.com", "admin_receiver")
    _add(db, "m1@x.com", "admin_master", allowed_systems=("legacy", "new"))
    _add(db, "school@x.com", "school", allowed_systems=("new",))  # 新システム専用 → 対象外
    db.commit()

    assigned = assign_missing_user_nos(db)
    db.commit()

    by_email = {u.email: u.user_no for u in db.query(User).all()}
    assert by_email["t1@x.com"] == "T1001"
    assert by_email["t2@x.com"] == "T1002"
    assert by_email["p1@x.com"] == "T2001"
    assert by_email["r1@x.com"] == "T3001"
    assert by_email["m1@x.com"] == "T3002"
    assert by_email["school@x.com"] is None
    assert assigned == 5


def test_assign_is_idempotent(db):
    _add(db, "t1@x.com", "tutor", tutor_no="T005")
    db.commit()
    assert assign_missing_user_nos(db) == 1
    db.commit()
    # 2回目は既に付与済みのため0件
    assert assign_missing_user_nos(db) == 0


def test_generate_user_no_continues_band(db):
    _add(db, "p1@x.com", "parent", user_no="T2001")
    db.commit()
    assert generate_user_no(db, "parent") == "T2002"
    assert generate_user_no(db, "tutor") == "T1001"
