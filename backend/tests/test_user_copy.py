"""ユーザーのコピー新規登録 API（POST /api/users/copy・改修依頼 202607210807 既存システム①）と、
削除によるメールアドレス解放（同 ②）のテスト。

コピーは招待メールを送らず直接作成する（初期パスワード Passw0rd!・初回変更必須）。
実メールは送出しない（conftest で MAIL_BACKEND=console・招待APIも呼ばない）。
"""
import uuid

from sqlalchemy import select

from app.core.security import verify_password
from app.database import SessionLocal
from app.models import User
from tests.conftest import token


def _headers(client, email):
    return {"Authorization": f"Bearer {token(client, email)}"}


def _get_user(email):
    db = SessionLocal()
    try:
        return db.scalar(select(User).where(User.email == email.lower()))
    finally:
        db.close()


def _copy(client, actor, source_id, **body):
    """actor=操作者のメール。body は display_name / email（新規ユーザーの入力）。"""
    return client.post(
        "/api/users/copy",
        headers=_headers(client, actor),
        json={"source_user_id": str(source_id), **body},
    )


def _source(db, email="tutor@example.com"):
    return db.query(User).filter(User.email == email).one()


# --- コピー新規登録 ---

def test_copy_creates_direct_user_without_invitation(client, db):
    source = _source(db)
    res = _copy(client, "master@example.com", source.id,
                display_name="コピー講師", email="Copy.Tutor@example.com")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["display_name"] == "コピー講師"
    assert body["email"] == "copy.tutor@example.com"  # 小文字化して保存
    assert body["role"] == "tutor"
    assert body["roles"] == ["tutor"]
    assert body["is_active"] is True

    created = _get_user("copy.tutor@example.com")
    assert created.must_change_password is True
    assert verify_password("Passw0rd!", created.password_hash)
    assert created.user_no and created.user_no.startswith("1")   # 講師は1万台
    assert created.tutor_no == created.user_no
    assert created.user_no != source.user_no
    assert created.allowed_systems == ["legacy"]


def test_copy_replicates_roles_and_skip_flag(client, db):
    parent = _source(db, "parent@example.com")
    parent.skip_parent_approval = True
    db.commit()
    res = _copy(client, "master@example.com", parent.id,
                display_name="コピー保護者", email="copy.parent@example.com")
    assert res.status_code == 201, res.text
    assert res.json()["skip_parent_approval"] is True
    created = _get_user("copy.parent@example.com")
    assert created.roles == ["parent"]
    # 担当（生徒）は引き継がない
    from app.models import Assignment
    assert db.query(Assignment).filter(Assignment.parent_id == created.id).count() == 0


def test_copy_rejects_duplicate_name(client, db):
    source = _source(db)
    res = _copy(client, "master@example.com", source.id,
                display_name="Parent", email="dup-name@example.com")
    assert res.status_code == 409, res.text
    assert "氏名" in res.json()["detail"]
    assert _get_user("dup-name@example.com") is None


def test_copy_rejects_duplicate_email(client, db):
    source = _source(db)
    res = _copy(client, "master@example.com", source.id,
                display_name="別の講師", email="Parent@example.com")
    assert res.status_code == 409, res.text
    assert "メール" in res.json()["detail"]


def test_copy_rejects_blank_name(client, db):
    source = _source(db)
    res = _copy(client, "master@example.com", source.id, display_name="   ", email="blank@example.com")
    assert res.status_code == 422


def test_copy_source_not_found(client, db):
    res = _copy(client, "master@example.com", uuid.uuid4(), display_name="誰か", email="who@example.com")
    assert res.status_code == 404


def test_copy_requires_admin_role(client, db):
    source = _source(db)
    res = _copy(client, "tutor@example.com", source.id, display_name="無権限", email="no@example.com")
    assert res.status_code == 403


# --- 削除によるメールアドレス解放（202607210807 ②）---

def test_delete_releases_email_and_allows_recreate(client, db):
    """削除したユーザーのアドレスは解放され、同じアドレスでコピー作成できる。"""
    target = _source(db, "receiver@example.com")
    target_id = target.id
    res = client.delete(f"/api/users/{target_id}", headers=_headers(client, "master@example.com"))
    assert res.status_code == 200, res.text

    db.expire_all()
    row = db.get(User, target_id)
    assert row is not None                       # 行は残る（履歴・監査ログの参照整合性）
    assert row.deleted_at is not None
    assert row.is_active is False
    assert row.email.endswith("@deleted.invalid")  # メールは解放済み

    source = _source(db)
    res = _copy(client, "master@example.com", source.id,
                display_name="後任講師", email="receiver@example.com")
    assert res.status_code == 201, res.text
    created = _get_user("receiver@example.com")
    assert created.id != target_id               # 復活ではなく別アカウント
