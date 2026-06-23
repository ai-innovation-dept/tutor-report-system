from app.models import Assignment, Invitation, User
from tests.conftest import token


def test_admin_can_create_and_resend_invitation(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()

    payload = {"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒"}
    created = client.post("/api/invitations", headers={"Authorization": f"Bearer {master_token}"}, json=payload)
    assert created.status_code == 200
    invitation = db.query(Invitation).filter(Invitation.email == "invitee@example.com").one()
    first_token = invitation.token
    assert invitation.assignment_id is not None
    assert invitation.assignment.student_name == "招待 生徒"
    assert created.json()["student_name"] == "招待 生徒"
    assert created.json()["tutor_id"] == str(tutor.id)
    assert created.json()["tutor_name"] == tutor.display_name
    assert sent and sent[-1][0] == "invitee@example.com"
    assert f"担当講師：{tutor.display_name}" in sent[-1][2]
    assert "担当生徒：招待 生徒" in sent[-1][2]

    resent = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒 再送"},
    )
    assert resent.status_code == 200
    db.expire_all()
    invitations = db.query(Invitation).filter(Invitation.email == "invitee@example.com").all()
    assert len(invitations) == 1
    assert invitations[0].token != first_token
    assert invitations[0].assignment.student_name == "招待 生徒 再送"


def test_admin_can_add_student_to_existing_parent(client, db, monkeypatch):
    async def fake_send(self, to, subject, body):
        raise AssertionError("existing parent should not receive invitation email")

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    parent = db.query(User).filter(User.email == "parent@example.com").one()

    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": parent.email, "tutor_id": str(tutor.id), "student_name": "追加 生徒"},
    )
    assert res.status_code == 200
    assert res.json()["message"] == "既存の保護者アカウントに生徒を紐付けました"
    assignment = db.query(Assignment).filter(Assignment.student_name == "追加 生徒").one()
    assert assignment.parent_id == parent.id
    assert assignment.tutor_id == tutor.id
    invitation = db.query(Invitation).filter(Invitation.assignment_id == assignment.id).one()
    assert invitation.accepted_at is not None


def test_admin_can_invite_tutor_and_register(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    existing_tutor = db.query(User).filter(User.role == "tutor").first()
    existing_tutor.tutor_no = "10001"
    db.commit()
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "tutor3@example.com", "role": "tutor", "display_name": "田中三郎"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "tutor"
    # 統一採番ポリシー: 既存講師が10001なので、最小の空きは10002（max+1ではなく歯抜けを埋める）。
    assert res.json()["tutor_no"] == "10002"
    assert "講師No：10002" in sent[-1][2]

    invitation = db.query(Invitation).filter(Invitation.email == "tutor3@example.com").one()
    info = client.get(f"/api/auth/register?token={invitation.token}")
    assert info.status_code == 200
    assert info.json()["role"] == "tutor"
    assert info.json()["display_name"] == "田中三郎"

    registered = client.post("/api/auth/register", json={
        "token": invitation.token,
        "display_name": "田中 三郎",
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    user = db.query(User).filter(User.email == "tutor3@example.com").one()
    assert user.roles == ["tutor"]
    assert user.display_name == "田中 三郎"
    assert user.tutor_no == "10002"


def test_admin_can_invite_staff_and_register(client, db, monkeypatch):
    sent = []

    async def fake_send(self, to, subject, body):
        sent.append((to, subject, body))

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "receiver2@example.com", "role": "admin_receiver", "display_name": "山田受付"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "admin_receiver"
    assert "ロール：受付担当" in sent[-1][2]

    invitation = db.query(Invitation).filter(Invitation.email == "receiver2@example.com").one()
    registered = client.post("/api/auth/register", json={
        "token": invitation.token,
        "password": "Passw0rd!",
    })
    assert registered.status_code == 200
    user = db.query(User).filter(User.email == "receiver2@example.com").one()
    assert user.roles == ["admin_receiver"]
    assert user.display_name == "山田受付"


def test_parent_cannot_create_invitation(client, db):
    parent_token = token(client, "parent@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {parent_token}"},
        json={"email": "invitee@example.com", "tutor_id": str(tutor.id), "student_name": "招待 生徒"},
    )
    assert res.status_code == 403


def test_invite_reuses_parentless_assignment_no_duplicate(client, db, monkeypatch):
    """保護者未設定の既存担当に保護者を招待すると、重複を作らず既存担当を再利用して紐づく。"""
    async def fake_send(self, to, subject, body):
        pass

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    orphan = Assignment(tutor_id=tutor.id, parent_id=None, student_name="二郎", is_active=True)
    db.add(orphan)
    db.commit()
    db.refresh(orphan)

    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": parent.email, "tutor_id": str(tutor.id), "student_name": "二郎"},
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    matches = db.query(Assignment).filter(Assignment.tutor_id == tutor.id, Assignment.student_name == "二郎").all()
    assert len(matches) == 1  # 重複担当が作られない
    assert matches[0].id == orphan.id
    assert matches[0].parent_id == parent.id


def test_invite_conflicts_when_student_already_has_other_parent(client, db, monkeypatch):
    """同一(講師,生徒名)が別の保護者に紐づき済みなら、招待は409で拒否（誤った付け替え防止）。"""
    async def fake_send(self, to, subject, body):
        pass

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")
    tutor = db.query(User).filter(User.role == "tutor").first()
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    db.add(Assignment(tutor_id=tutor.id, parent_id=parent.id, student_name="三郎", is_active=True))
    db.commit()

    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "another-parent@example.com", "tutor_id": str(tutor.id), "student_name": "三郎"},
    )
    assert res.status_code == 409, res.text


def test_deleted_user_can_be_reinvited_and_revived(client, db, monkeypatch):
    """削除済み（ソフトデリート）ユーザーは再招待でき、登録で同一アカウントが復活する。"""
    from datetime import datetime, timezone

    async def fake_send(self, to, subject, body):
        pass

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")

    # 講師を削除（ソフトデリート）
    tutor = db.query(User).filter(User.email == "tutor@example.com").one()
    old_id = tutor.id
    tutor.is_active = False
    tutor.deleted_at = datetime.now(timezone.utc)
    db.commit()

    # 在籍中なら409だが、削除済みは再招待できる
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "tutor@example.com", "role": "tutor", "display_name": "復職講師"},
    )
    assert res.status_code == 200, res.text

    invitation = db.query(Invitation).filter(
        Invitation.email == "tutor@example.com", Invitation.accepted_at.is_(None)
    ).one()
    registered = client.post("/api/auth/register", json={
        "token": invitation.token,
        "display_name": "復職講師",
        "password": "NewPass!!",
    })
    assert registered.status_code == 200, registered.text

    db.expire_all()
    user = db.query(User).filter(User.email == "tutor@example.com").one()
    assert user.id == old_id              # 同一アカウント（履歴は引き継がれる）
    assert user.deleted_at is None
    assert user.is_active is True
    assert user.roles == ["tutor"]
    assert user.tutor_no == invitation.tutor_no  # 新しい招待のNoを採用

    # 新しいパスワードでログインできる
    login = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "NewPass!!"})
    assert login.status_code == 200

    # 旧パスワードは使えない
    bad = client.post("/api/auth/login", data={"username": "tutor@example.com", "password": "Passw0rd!"})
    assert bad.status_code == 401


def test_deleted_parent_can_be_reinvited_and_revived(client, db, monkeypatch):
    """削除済みの保護者も再招待→登録で復活し、担当・報告書に再紐付けされる。"""
    from datetime import datetime, timezone

    async def fake_send(self, to, subject, body):
        pass

    monkeypatch.setattr("app.api.invitations.EmailChannel.send", fake_send)
    master_token = token(client, "master@example.com")

    parent = db.query(User).filter(User.email == "parent@example.com").one()
    old_id = parent.id
    parent.is_active = False
    parent.deleted_at = datetime.now(timezone.utc)
    db.commit()

    tutor = db.query(User).filter(User.role == "tutor").first()
    res = client.post(
        "/api/invitations",
        headers={"Authorization": f"Bearer {master_token}"},
        json={"email": "parent@example.com", "tutor_id": str(tutor.id), "student_name": "復活生徒"},
    )
    assert res.status_code == 200, res.text
    # 削除済みは「既存アカウントへ自動紐付け」ではなく通常の招待メール送付になる
    assert res.json()["message"] != "既存の保護者アカウントに生徒を紐付けました"

    invitation = db.query(Invitation).filter(
        Invitation.email == "parent@example.com", Invitation.accepted_at.is_(None)
    ).one()
    registered = client.post("/api/auth/register", json={
        "token": invitation.token,
        "password": "NewPass!!",
    })
    assert registered.status_code == 200, registered.text

    db.expire_all()
    user = db.query(User).filter(User.email == "parent@example.com").one()
    assert user.id == old_id
    assert user.deleted_at is None
    assert user.is_active is True
    assert user.roles == ["parent"]
    assignment = db.query(Assignment).filter(Assignment.student_name == "復活生徒").one()
    assert assignment.parent_id == user.id
