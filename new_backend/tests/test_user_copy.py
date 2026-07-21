"""ユーザーのコピー新規登録 API（POST /api/w/users/copy・改修依頼 202607171557）のテスト。

コピーは招待メールを送らず直接作成する（初期パスワード Passw0rd!・初回変更必須）。
本テストは実メールを送出しない（MAIL_BACKEND=console 既定＋送信キューが空であることを確認）。
"""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.main import app
from app.models.shared import User
from app.models.work import WorkMailOutbox, WorkSchoolDeadline, WorkSchoolSetting
from app.services import school_deadline_service
from tests.conftest import TestSession


@pytest.fixture()
def db():
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client():
    return TestClient(app)


def _add_user(db, email, role, *, display_name=None, roles=None, allowed_systems=None, skip=False):
    user = User(
        email=email,
        role=role,
        roles=roles or [role],
        display_name=display_name or f"{role}ユーザー",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=allowed_systems or ["new"],
        skip_parent_approval=skip,
    )
    db.add(user)
    db.commit()
    return user


def _auth(client, email):
    res = client.post("/api/auth/login", json={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _copy(client, headers, source, **body):
    payload = {"source_user_id": str(source.id), **body}
    return client.post("/api/w/users/copy", json=payload, headers=headers)


class TestUserCopy:
    def test_copy_creates_direct_user_without_email(self, db, client):
        _add_user(db, "master@c.example.com", "admin_master")
        source = _add_user(db, "src@c.example.com", "office", display_name="コピー元事務")
        headers = _auth(client, "master@c.example.com")

        res = _copy(client, headers, source, display_name="新規事務", email="New.Office@c.example.com")
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["display_name"] == "新規事務"
        assert body["email"] == "new.office@c.example.com"  # 小文字化して保存
        assert body["role"] == "office"
        assert body["roles"] == ["office"]
        assert body["user_no"] and body["user_no"].startswith("5")  # 事務は5万台
        assert body["is_active"] is True

        created = db.scalar(select(User).where(User.email == "new.office@c.example.com"))
        assert created.must_change_password is True
        assert verify_password("Passw0rd!", created.password_hash)
        # コピー元とは別Noを採番する
        assert created.user_no != source.user_no
        # 招待メールを送らない＝送信キューは空（実メールは飛ばない）
        assert db.query(WorkMailOutbox).count() == 0

    def test_copy_replicates_multi_roles_and_systems(self, db, client):
        _add_user(db, "master2@c.example.com", "admin_master")
        source = _add_user(
            db, "multi@c.example.com", "office",
            display_name="兼務ユーザー", roles=["office", "sales"], allowed_systems=["new", "legacy"],
        )
        headers = _auth(client, "master2@c.example.com")

        res = _copy(client, headers, source, display_name="兼務コピー", email="multi2@c.example.com")
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["roles"] == ["office", "sales"]
        assert body["allowed_systems"] == ["new", "legacy"]

    def test_copy_school_replicates_skip_flag(self, db, client):
        _add_user(db, "master3@c.example.com", "admin_master")
        source = _add_user(db, "school-src@c.example.com", "school", display_name="スキップ校", skip=True)
        headers = _auth(client, "master3@c.example.com")

        res = _copy(client, headers, source, display_name="スキップ校コピー", email="school-copy@c.example.com")
        assert res.status_code == 201, res.text
        assert res.json()["skip_parent_approval"] is True
        created = db.scalar(select(User).where(User.email == "school-copy@c.example.com"))
        assert created.user_no and created.user_no.startswith("4")  # 学校は4万台

    def test_copy_school_replicates_deadline_settings(self, db, client):
        """学校の締め日（年間設定）と早期チェック・通知日数もコピーする（202607210807 ①）。"""
        _add_user(db, "master-dl@c.example.com", "admin_master")
        source = _add_user(db, "school-dl@c.example.com", "school", display_name="締め日あり校")
        client_headers = _auth(client, "master-dl@c.example.com")
        # 締め日設定は保存APIと同じ経路（サービス）で作る＝実運用と同じ状態
        school_deadline_service.save_school_settings(
            db, source,
            early_check_enabled=True,
            notice_days_before=5,
            deadlines={"2026-06": date(2026, 6, 25), "2026-07": date(2026, 7, 24)},
        )
        db.commit()

        res = _copy(client, client_headers, source, display_name="締め日コピー校", email="school-dl2@c.example.com")
        assert res.status_code == 201, res.text
        created = db.scalar(select(User).where(User.email == "school-dl2@c.example.com"))

        setting = db.scalar(select(WorkSchoolSetting).where(WorkSchoolSetting.school_id == created.id))
        assert setting is not None
        assert setting.early_check_enabled is True
        assert setting.notice_days_before == 5
        copied = db.scalars(
            select(WorkSchoolDeadline).where(WorkSchoolDeadline.school_id == created.id)
            .order_by(WorkSchoolDeadline.target_month)
        ).all()
        assert [(d.target_month, d.deadline_date) for d in copied] == [
            ("2026-06", date(2026, 6, 25)), ("2026-07", date(2026, 7, 24)),
        ]
        # 送信済みガードは引き継がない（コピー先はこれから通知対象）
        assert all(d.notice_sent_at is None for d in copied)
        # コピー元の設定は変わらない
        assert db.scalars(
            select(WorkSchoolDeadline).where(WorkSchoolDeadline.school_id == source.id)
        ).all().__len__() == 2

    def test_copy_non_school_creates_no_deadline_settings(self, db, client):
        """学校以外のロールでは締め日設定を作らない（不要な行を増やさない）。"""
        _add_user(db, "master-dl2@c.example.com", "admin_master")
        source = _add_user(db, "office-dl@c.example.com", "office", display_name="事務コピー元DL")
        res = _copy(client, _auth(client, "master-dl2@c.example.com"), source,
                    display_name="事務コピー先DL", email="office-dl2@c.example.com")
        assert res.status_code == 201, res.text
        created = db.scalar(select(User).where(User.email == "office-dl2@c.example.com"))
        assert db.scalar(select(WorkSchoolSetting).where(WorkSchoolSetting.school_id == created.id)) is None

    def test_copy_tutor_assigns_tutor_no(self, db, client):
        _add_user(db, "master4@c.example.com", "admin_master")
        source = _add_user(db, "tutor-src@c.example.com", "tutor", display_name="講師コピー元")
        headers = _auth(client, "master4@c.example.com")

        res = _copy(client, headers, source, display_name="講師コピー先", email="tutor-copy@c.example.com")
        assert res.status_code == 201, res.text
        created = db.scalar(select(User).where(User.email == "tutor-copy@c.example.com"))
        assert created.user_no and created.user_no.startswith("1")  # 講師は1万台
        assert created.tutor_no == created.user_no  # legacy 互換

    def test_duplicate_name_rejected(self, db, client):
        _add_user(db, "master5@c.example.com", "admin_master")
        source = _add_user(db, "src5@c.example.com", "office", display_name="コピー元")
        _add_user(db, "existing5@c.example.com", "office", display_name="既に居る氏名")
        headers = _auth(client, "master5@c.example.com")

        res = _copy(client, headers, source, display_name="既に居る氏名", email="new5@c.example.com")
        assert res.status_code == 409, res.text
        assert "氏名" in res.json()["detail"]
        assert db.scalar(select(User).where(User.email == "new5@c.example.com")) is None

    def test_duplicate_email_rejected(self, db, client):
        _add_user(db, "master6@c.example.com", "admin_master")
        source = _add_user(db, "src6@c.example.com", "office", display_name="コピー元6")
        _add_user(db, "taken6@c.example.com", "office", display_name="既存6")
        headers = _auth(client, "master6@c.example.com")

        res = _copy(client, headers, source, display_name="新規6", email="Taken6@c.example.com")
        assert res.status_code == 409, res.text
        assert "メール" in res.json()["detail"]

    def test_source_not_found(self, db, client):
        _add_user(db, "master7@c.example.com", "admin_master")
        headers = _auth(client, "master7@c.example.com")
        res = client.post("/api/w/users/copy", json={
            "source_user_id": str(uuid.uuid4()), "display_name": "誰か", "email": "who7@c.example.com",
        }, headers=headers)
        assert res.status_code == 404

    def test_blank_name_rejected(self, db, client):
        _add_user(db, "master8@c.example.com", "admin_master")
        source = _add_user(db, "src8@c.example.com", "office", display_name="元8")
        headers = _auth(client, "master8@c.example.com")
        res = _copy(client, headers, source, display_name="   ", email="blank8@c.example.com")
        assert res.status_code == 422

    def test_requires_staff_role(self, db, client):
        source = _add_user(db, "src9@c.example.com", "office", display_name="元9")
        _add_user(db, "tutor9@c.example.com", "tutor")
        headers = _auth(client, "tutor9@c.example.com")
        res = _copy(client, headers, source, display_name="無権限9", email="no9@c.example.com")
        assert res.status_code == 403

    def test_admin_chief_copy_requires_chief(self, db, client):
        _add_user(db, "office10@c.example.com", "office")
        _add_user(db, "chief10@c.example.com", "admin_chief", allowed_systems=["new", "legacy"])
        source = _add_user(db, "chief-src10@c.example.com", "admin_chief",
                           display_name="管理責任者元", allowed_systems=["new", "legacy"])

        # 事務はコピー不可（職務分掌）
        res = _copy(client, _auth(client, "office10@c.example.com"), source,
                    display_name="責任者コピーNG", email="ng10@c.example.com")
        assert res.status_code == 403

        # 管理責任者はコピー可
        res = _copy(client, _auth(client, "chief10@c.example.com"), source,
                    display_name="責任者コピーOK", email="ok10@c.example.com")
        assert res.status_code == 201, res.text
        assert res.json()["roles"] == ["admin_chief"]
