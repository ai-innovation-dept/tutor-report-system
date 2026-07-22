"""改修依頼 202607221522 ①: 無効化ユーザー＝削除と同等に「宛先から外す」＝メールを送らない。

- 無効化（is_active=False）した保護者には、承認フローの通知メールもパスワードリセットも
  送らない（送信キュー mail_outbox に行が作られない）。
- 有効化で元に戻せるよう行・メールアドレスは保持する（本テストは「送らない」ことの検証）。
- 実 SMTP は呼ばない（MAIL_BACKEND=console・enqueue は outbox 行を作るだけ）。
- 有効なユーザーには従来どおり投函されること（＝ガードが通常送信を壊していないこと）も反証として確認する。
"""
from app.core.time import get_current_jst_date
from app.models import Assignment, MailOutbox, PasswordResetToken, User
from tests.conftest import token


def _create_and_submit(client, db, tutor_token) -> str:
    """報告書を1件作成し、承認依頼（submit-to-parent）まで進める。rid を返す。"""
    assignment = db.query(Assignment).first()
    today = get_current_jst_date()
    rid = client.post(
        "/api/reports",
        headers={"Authorization": f"Bearer {tutor_token}"},
        json={
            "assignment_id": str(assignment.id),
            "lesson_date": str(today),
            "start_time": "18:00",
            "end_time": "19:00",
            "subject": "math",
            "content": "lesson",
        },
    ).json()["id"]
    res = client.post(
        f"/api/reports/{rid}/submit-to-parent",
        headers={"Authorization": f"Bearer {tutor_token}"},
        json={},
    )
    assert res.status_code == 200, res.text
    return rid


def _outbox_count(db, email: str) -> int:
    return db.query(MailOutbox).filter(MailOutbox.to_email == email).count()


def test_active_parent_receives_workflow_email(client, db):
    """反証: 有効な保護者には承認依頼メールが投函される（ガードが通常送信を壊していない）。"""
    tutor_token = token(client, "tutor@example.com")
    _create_and_submit(client, db, tutor_token)
    assert _outbox_count(db, "parent@example.com") >= 1


def test_disabled_parent_receives_no_workflow_email(client, db):
    """無効化した保護者には承認依頼メールを送らない（＝outbox に行が作られない）。"""
    tutor_token = token(client, "tutor@example.com")
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    parent.is_active = False
    db.commit()
    # 承認依頼の遷移自体は成立するが、宛先（無効保護者）へのメールは投函されない
    _create_and_submit(client, db, tutor_token)
    assert _outbox_count(db, "parent@example.com") == 0


def test_forgot_password_skips_disabled_user(client, db):
    """無効化ユーザーにはリセットメール・トークンを発行しない（応答文言は有効時と同一で存在を明かさない）。"""
    parent = db.query(User).filter(User.email == "parent@example.com").one()
    parent.is_active = False
    db.commit()
    res = client.post("/api/auth/forgot-password", json={"email": "parent@example.com"})
    assert res.status_code == 200
    assert _outbox_count(db, "parent@example.com") == 0
    assert db.query(PasswordResetToken).count() == 0


def test_forgot_password_active_user_still_sends(client, db):
    """反証: 有効ユーザーには従来どおりリセットメールを投函する。"""
    res = client.post("/api/auth/forgot-password", json={"email": "parent@example.com"})
    assert res.status_code == 200
    assert _outbox_count(db, "parent@example.com") >= 1
