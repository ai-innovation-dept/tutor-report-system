"""
招待・登録・パスワードリセット・ユーザー管理の統合テスト。
メール送信は monkeypatch でスタブ化する。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import Invitation, User
from app.services.user_service import ROLE_LABELS
from tests.conftest import TestSession

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_send_email():
    """メール送信のスタブ（現在は不要なので no-op）。

    メールは即時送信せず送信キュー(work_mail_outbox)へ投函するだけになり、テストは
    MAIL_BACKEND=console かつドレイナ未起動のため実送信は発生しない。よって特別な
    スタブは不要。互換のためフィクスチャ自体は残す。
    """
    yield


@pytest.fixture()
def master_user():
    db = TestSession()
    u = User(
        email="master@new.example.com",
        role="admin_master",
        roles=["admin_master"],
        display_name="管理者",
        password_hash=hash_password("Passw0rd!"),
        allowed_systems=["legacy", "new"],
    )
    db.add(u)
    db.commit()
    db.close()
    return u


@pytest.fixture()
def client():
    return TestClient(app)


def _auth(client, email="master@new.example.com", password="Passw0rd!"):
    res = client.post("/api/auth/login", json={"username": email, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


# ---------------------------------------------------------------------------
# 招待作成
# ---------------------------------------------------------------------------

class TestInvitationCreate:
    def _invite(self, client, role, master_user, extra=None):
        headers = _auth(client)
        payload = {"role": role, "email": f"{role}@invite.example.com", "display_name": f"テスト{ROLE_LABELS.get(role, role)}"}
        if extra:
            payload.update(extra)
        return client.post("/api/w/invitations", json=payload, headers=headers)

    def test_invite_tutor_ok(self, client, master_user):
        res = self._invite(client, "tutor", master_user)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["role"] == "tutor"
        assert data["user_no"].isdigit()
        assert int(data["user_no"]) >= 10001

    def test_invite_school_ok(self, client, master_user):
        res = self._invite(client, "school", master_user)
        assert res.status_code == 201
        data = res.json()
        assert data["user_no"].isdigit()
        assert int(data["user_no"]) >= 40001

    def test_invite_sales_ok(self, client, master_user):
        res = self._invite(client, "sales", master_user)
        assert res.status_code == 201
        assert res.json()["user_no"].isdigit()
        assert int(res.json()["user_no"]) >= 50001

    def test_invite_office_ok(self, client, master_user):
        res = self._invite(client, "office", master_user)
        assert res.status_code == 201
        assert res.json()["user_no"].isdigit()
        assert int(res.json()["user_no"]) >= 50001

    def test_invite_admin_master_ok(self, client, master_user):
        res = self._invite(client, "admin_master", master_user)
        assert res.status_code == 201
        assert res.json()["user_no"].isdigit()
        assert int(res.json()["user_no"]) >= 50001

    def test_non_admin_cannot_invite(self, client, master_user):
        db = TestSession()
        tutor = User(email="t@new.example.com", role="tutor", roles=["tutor"], display_name="講師",
                     password_hash=hash_password("Passw0rd!"), allowed_systems=["new"])
        db.add(tutor); db.commit(); db.close()
        headers = _auth(client, "t@new.example.com")
        res = client.post("/api/w/invitations", json={"role": "tutor", "email": "x@x.com"}, headers=headers)
        assert res.status_code == 403

    def test_duplicate_email_returns_409(self, client, master_user):
        db = TestSession()
        existing = User(email="exists@new.example.com", role="tutor", roles=["tutor"],
                        display_name="既存", password_hash=hash_password("Passw0rd!"), allowed_systems=["new"])
        db.add(existing); db.commit(); db.close()
        res = client.post("/api/w/invitations",
                          json={"role": "tutor", "email": "exists@new.example.com"},
                          headers=_auth(client))
        assert res.status_code == 409

    def test_invalid_role_returns_422(self, client, master_user):
        res = client.post("/api/w/invitations",
                          json={"role": "parent", "email": "p@x.com"},
                          headers=_auth(client))
        assert res.status_code == 422

    def test_user_no_increments(self, client, master_user):
        """同ロールを連続招待するとNoが増加する"""
        h = _auth(client)
        r1 = client.post("/api/w/invitations", json={"role": "school", "email": "s1@x.example.com", "display_name": "学校1"}, headers=h)
        r2 = client.post("/api/w/invitations", json={"role": "school", "email": "s2@x.example.com", "display_name": "学校2"}, headers=h)
        assert r1.status_code == 201 and r2.status_code == 201
        n1 = int(r1.json()["user_no"])
        n2 = int(r2.json()["user_no"])
        assert n2 == n1 + 1


# ---------------------------------------------------------------------------
# 招待一覧・削除
# ---------------------------------------------------------------------------

class TestInvitationListDelete:
    def test_admin_can_list(self, client, master_user):
        headers = _auth(client)
        client.post("/api/w/invitations", json={"role": "tutor", "email": "tl@x.example.com"}, headers=headers)
        res = client.get("/api/w/invitations", headers=headers)
        assert res.status_code == 200
        assert len(res.json()) >= 1

    def test_can_delete_pending(self, client, master_user):
        headers = _auth(client)
        r = client.post("/api/w/invitations", json={"role": "tutor", "email": "td@x.example.com"}, headers=headers)
        inv_id = r.json()["id"]
        res = client.delete(f"/api/w/invitations/{inv_id}", headers=headers)
        assert res.status_code == 200

    def test_cannot_delete_accepted(self, client, master_user):
        from datetime import datetime, timezone
        db = TestSession()
        inv = Invitation(
            email="accepted@x.example.com",
            role="tutor",
            token="accepted-token-123",
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            accepted_at=datetime.now(timezone.utc),
        )
        db.add(inv); db.commit()
        inv_id = str(inv.id); db.close()
        res = client.delete(f"/api/w/invitations/{inv_id}", headers=_auth(client))
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# 登録フロー
# ---------------------------------------------------------------------------

class TestRegistration:
    def _create_invitation(self, role="tutor", email="reg@x.example.com"):
        db = TestSession()
        from datetime import datetime, timedelta, timezone
        import secrets
        from app.services.user_service import generate_user_no
        user_no = generate_user_no(db, role)
        inv = Invitation(
            email=email,
            role=role,
            display_name="テスト登録",
            tutor_no=user_no,
            token=secrets.token_urlsafe(16),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        )
        db.add(inv); db.commit()
        token = inv.token; db.close()
        return token

    def test_register_info_ok(self, client, master_user):
        token = self._create_invitation()
        res = client.get(f"/api/auth/register?token={token}")
        assert res.status_code == 200
        data = res.json()
        assert data["role"] == "tutor"
        assert data["user_no"].isdigit()
        assert int(data["user_no"]) >= 10001

    def test_register_info_invalid_token(self, client, master_user):
        res = client.get("/api/auth/register?token=invalid")
        assert res.status_code == 404

    def test_register_creates_user(self, client, master_user):
        token = self._create_invitation(role="school", email="school_reg@x.example.com")
        res = client.post("/api/auth/register",
                          json={"token": token, "password": "Passw0rd!!", "display_name": "学校担当テスト"})
        assert res.status_code == 200, res.text

        # ユーザーが作成されたか確認
        db = TestSession()
        user = db.scalar(
            __import__("sqlalchemy").select(User).where(User.email == "school_reg@x.example.com")
        )
        assert user is not None
        assert user.role == "school"
        assert user.user_no.isdigit()
        assert int(user.user_no) >= 40001
        assert user.allowed_systems == ["new"]
        db.close()

    def test_register_admin_master_gets_new_system(self, client, master_user):
        token = self._create_invitation(role="admin_master", email="am_reg@x.example.com")
        client.post("/api/auth/register",
                    json={"token": token, "password": "Passw0rd!!", "display_name": "管理者テスト"})
        db = TestSession()
        from sqlalchemy import select as sa_select
        user = db.scalar(sa_select(User).where(User.email == "am_reg@x.example.com"))
        # admin_master は常に両システム所属で登録される。
        assert set(user.allowed_systems) == {"legacy", "new"}
        db.close()

    def test_register_used_token_returns_409(self, client, master_user):
        token = self._create_invitation(email="used@x.example.com")
        client.post("/api/auth/register", json={"token": token, "password": "Passw0rd!!", "display_name": "A"})
        res = client.post("/api/auth/register", json={"token": token, "password": "Passw0rd!!", "display_name": "B"})
        assert res.status_code == 409


# ---------------------------------------------------------------------------
# 削除済みユーザーの再登録（同一アカウント復活）
# ---------------------------------------------------------------------------

class TestDeletedUserReRegistration:
    EMAIL = "rejoin@x.example.com"

    def _add_deleted_user(self, role="office"):
        from datetime import datetime, timezone
        db = TestSession()
        u = User(
            email=self.EMAIL, role=role, roles=[role], display_name="退職済み",
            password_hash=hash_password("OldPass!!"), allowed_systems=["new"],
            user_no="50090", is_active=False,
            deleted_at=datetime.now(timezone.utc),
        )
        db.add(u); db.commit()
        user_id = str(u.id); db.close()
        return user_id

    def test_active_user_cannot_be_reinvited(self, client, master_user):
        db = TestSession()
        db.add(User(email=self.EMAIL, role="office", roles=["office"], display_name="在籍中",
                    password_hash=hash_password("P!"), allowed_systems=["new"]))
        db.commit(); db.close()
        res = client.post("/api/w/invitations",
                          json={"role": "office", "email": self.EMAIL},
                          headers=_auth(client))
        assert res.status_code == 409

    def test_deleted_user_can_be_reinvited(self, client, master_user):
        self._add_deleted_user()
        res = client.post("/api/w/invitations",
                          json={"role": "office", "email": self.EMAIL, "display_name": "再入社"},
                          headers=_auth(client))
        assert res.status_code == 201, res.text

    def test_deleted_user_register_revives_account(self, client, master_user):
        from sqlalchemy import select as sa_select
        old_id = self._add_deleted_user(role="office")

        # 別ロール（営業）で再招待 → 登録
        res = client.post("/api/w/invitations",
                          json={"role": "sales", "email": self.EMAIL, "display_name": "再入社"},
                          headers=_auth(client))
        assert res.status_code == 201, res.text
        new_user_no = res.json()["user_no"]

        db = TestSession()
        inv = db.scalar(sa_select(Invitation).where(
            Invitation.email == self.EMAIL, Invitation.accepted_at.is_(None)))
        token = inv.token; db.close()

        res = client.post("/api/auth/register",
                          json={"token": token, "password": "NewPass!!", "display_name": "再入社"})
        assert res.status_code == 200, res.text

        # 同一アカウントが復活し、招待内容で初期化されている
        db = TestSession()
        user = db.scalar(sa_select(User).where(User.email == self.EMAIL))
        assert str(user.id) == old_id          # 同一アカウント（履歴は引き継がれる）
        assert user.deleted_at is None
        assert user.is_active is True
        assert user.roles == ["sales"]         # 旧ロールは引き継がない
        assert user.user_no == new_user_no     # 新しい招待のNoを採用
        db.close()

        # 新しいパスワードでログインでき、旧パスワードは使えない
        ok = client.post("/api/auth/login", json={"username": self.EMAIL, "password": "NewPass!!"})
        assert ok.status_code == 200
        assert ok.json()["role"] == "sales"
        ng = TestClient(app).post("/api/auth/login", json={"username": self.EMAIL, "password": "OldPass!!"})
        assert ng.status_code == 401


# ---------------------------------------------------------------------------
# パスワードリセット
# ---------------------------------------------------------------------------

class TestPasswordReset:
    def test_forgot_password_silent_on_unknown_email(self, client, master_user):
        res = client.post("/api/auth/forgot-password", json={"email": "nobody@x.example.com"})
        assert res.status_code == 200
        assert "送信しました" in res.json()["message"]

    def test_reset_password_full_flow(self, client, master_user):
        from datetime import datetime, timedelta, timezone
        import secrets
        from app.models.shared import PasswordResetToken
        db = TestSession()
        from sqlalchemy import select as sa_select
        user = db.scalar(sa_select(User).where(User.email == "master@new.example.com"))
        rt = PasswordResetToken(
            user_id=user.id,
            token=secrets.token_urlsafe(16),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(rt); db.commit()
        token = rt.token; db.close()

        # トークン情報取得
        info = client.get(f"/api/auth/reset-password?token={token}")
        assert info.status_code == 200
        assert info.json()["valid"] is True

        # パスワード変更
        res = client.post("/api/auth/reset-password", json={"token": token, "new_password": "NewPass!!"})
        assert res.status_code == 200

        # 変更後ログイン
        login_res = client.post("/api/auth/login", json={"username": "master@new.example.com", "password": "NewPass!!"})
        assert login_res.status_code == 200

    def test_reset_password_invalid_token(self, client, master_user):
        res = client.get("/api/auth/reset-password?token=badtoken")
        assert res.status_code == 200
        assert res.json()["valid"] is False


# ---------------------------------------------------------------------------
# ユーザー管理
# ---------------------------------------------------------------------------

class TestUserManagement:
    def _add_user(self, email, role):
        db = TestSession()
        u = User(email=email, role=role, roles=[role], display_name="テスト",
                 password_hash=hash_password("P!"), allowed_systems=["new"])
        db.add(u); db.flush()
        uid = str(u.id)
        db.commit(); db.close()
        return uid

    def test_admin_can_list_users(self, client, master_user):
        self._add_user("lst@x.example.com", "school")
        res = client.get("/api/w/users", headers=_auth(client))
        assert res.status_code == 200
        assert res.json()["total"] >= 2  # master + school

    def test_list_filter_by_role(self, client, master_user):
        self._add_user("sch@x.example.com", "school")
        res = client.get("/api/w/users?role=school", headers=_auth(client))
        assert res.status_code == 200
        items = res.json()["items"]
        assert all("school" in (u["roles"] or [u["role"]]) for u in items)

    def test_admin_can_disable_user(self, client, master_user):
        uid = self._add_user("dis@x.example.com", "tutor")
        res = client.patch(f"/api/w/users/{uid}", json={"is_active": False}, headers=_auth(client))
        assert res.status_code == 200
        assert res.json()["is_active"] is False

    def test_admin_can_reset_user_password(self, client, master_user):
        uid = self._add_user("rp@x.example.com", "tutor")
        res = client.post(f"/api/w/users/{uid}/reset-password", headers=_auth(client))
        assert res.status_code == 200
        assert "initial_password" in res.json()

    def test_non_admin_cannot_list_users(self, client, master_user):
        db = TestSession()
        u = User(email="t2@x.example.com", role="tutor", roles=["tutor"], display_name="T",
                 password_hash=hash_password("Passw0rd!"), allowed_systems=["new"])
        db.add(u); db.commit(); db.close()
        h = _auth(client, "t2@x.example.com")
        res = client.get("/api/w/users", headers=h)
        assert res.status_code == 403

    def test_sales_can_list_all_users(self, client, master_user):
        # 営業はユーザ管理を経理と同等に利用できる（「すべて」タブ＝フィルタなし一覧）
        db = TestSession()
        u = User(email="sales_mgr@x.example.com", role="sales", roles=["sales"], display_name="営業",
                 password_hash=hash_password("Passw0rd!"), allowed_systems=["new"])
        db.add(u); db.commit(); db.close()
        h = _auth(client, "sales_mgr@x.example.com")
        res = client.get("/api/w/users", headers=h)
        assert res.status_code == 200
        assert res.json()["total"] >= 2

    def test_office_can_list_users_and_invite(self, client, master_user):
        # 事務もユーザ管理（一覧・招待）を営業・経理と同等に利用できる
        db = TestSession()
        u = User(email="office_mgr@x.example.com", role="office", roles=["office"], display_name="事務",
                 password_hash=hash_password("Passw0rd!"), allowed_systems=["new"])
        db.add(u); db.commit(); db.close()
        h = _auth(client, "office_mgr@x.example.com")
        res = client.get("/api/w/users", headers=h)
        assert res.status_code == 200
        assert res.json()["total"] >= 2
        invited = client.post("/api/w/invitations",
                              json={"role": "tutor", "email": "office_invited@x.example.com"},
                              headers=h)
        assert invited.status_code == 201, invited.text
        # 管理責任者の招待は引き続き管理責任者のみ
        chief_invite = client.post("/api/w/invitations",
                                   json={"role": "admin_chief", "email": "x_chief@x.example.com"},
                                   headers=h)
        assert chief_invite.status_code == 403


# ---------------------------------------------------------------------------
# Assignment 管理
# ---------------------------------------------------------------------------

class TestAssignmentManagement:
    def _add_tutor(self, email="tat@x.example.com"):
        db = TestSession()
        u = User(email=email, role="tutor", roles=["tutor"], display_name="講師T",
                 password_hash=hash_password("P!"), allowed_systems=["new"])
        db.add(u); db.commit()
        uid = str(u.id); db.close()
        return uid

    def test_admin_can_create_assignment(self, client, master_user):
        tutor_id = self._add_tutor()
        res = client.post("/api/w/assignments",
                          json={"tutor_id": tutor_id, "student_name": "生徒A"},
                          headers=_auth(client))
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["system_type"] == "new"
        assert data["student_name"] == "生徒A"

    def test_duplicate_assignment_returns_409(self, client, master_user):
        tutor_id = self._add_tutor("dup@x.example.com")
        client.post("/api/w/assignments",
                    json={"tutor_id": tutor_id, "student_name": "生徒DP"},
                    headers=_auth(client))
        res = client.post("/api/w/assignments",
                          json={"tutor_id": tutor_id, "student_name": "生徒DP"},
                          headers=_auth(client))
        assert res.status_code == 409

    def test_admin_can_list_assignments(self, client, master_user):
        tutor_id = self._add_tutor("lst2@x.example.com")
        client.post("/api/w/assignments",
                    json={"tutor_id": tutor_id, "student_name": "生徒LIST"},
                    headers=_auth(client))
        res = client.get("/api/w/assignments", headers=_auth(client))
        assert res.status_code == 200
        assert len(res.json()) >= 1
