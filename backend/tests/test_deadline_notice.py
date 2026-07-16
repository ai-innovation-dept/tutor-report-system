# === 提出締切通知（改修依頼 202607161428） START ===
"""提出締切（翌月第一営業日）計算・講師向け締切バナー・締切メール通知のテスト。

メールは実送信しない（conftest で MAIL_BACKEND=console）。送信キュー(mail_outbox)への
投函とアプリ内通知ログ(notifications)・送信済みガード(deadline_notice_sends)を検証する。
"""
from datetime import date, datetime, time, timezone

from freezegun import freeze_time

from app.config import settings
from app.models import (
    Assignment,
    DeadlineNoticeSend,
    LessonReport,
    MailOutbox,
    Notification,
    ReportStatus,
    User,
)
from app.services.deadline_service import (
    DEADLINE_EVE_TYPE,
    DEADLINE_FIRST_TYPE,
    active_notice,
    due_email_notices,
    enqueue_deadline_notices,
    first_business_day,
    submission_deadline,
    unsubmitted_tutors,
)

FIXED_CREATED_AT = datetime(2026, 7, 1, tzinfo=timezone.utc)


# --- 締切（第一営業日）計算 ---

def test_first_business_day_skips_weekend():
    # 2026-08-01(土)・02(日) → 8/3(月)
    assert first_business_day(2026, 8) == date(2026, 8, 3)
    # 2026-09-01 は平日（火）
    assert first_business_day(2026, 9) == date(2026, 9, 1)


def test_first_business_day_respects_year_end_closure():
    # 年末年始休業（既定 12/29〜1/3）: 2027-01-01(金)〜03(日)休み → 1/4(月)
    assert first_business_day(2027, 1) == date(2027, 1, 4)


def test_first_business_day_skips_national_holidays():
    # 2027-05: 1(土) 2(日) 3(憲法記念日) 4(みどりの日) 5(こどもの日) → 5/6(木)
    assert first_business_day(2027, 5) == date(2027, 5, 6)


def test_submission_deadline_is_first_business_day_of_next_month():
    assert submission_deadline("2026-07") == date(2026, 8, 3)
    assert submission_deadline("2026-12") == date(2027, 1, 4)  # 年跨ぎ


# --- 画面バナーの表示期間（active_notice） ---

def test_active_notice_windows():
    assert active_notice(date(2026, 7, 14)) is None  # 月中通知日より前
    info = active_notice(date(2026, 7, 15))
    assert info["phase"] == "info"
    assert info["target_month"] == "2026-07"
    assert info["deadline_label"] == "8月3日（月）"
    assert active_notice(date(2026, 8, 1))["phase"] == "info"  # 翌月頭でも締切前々日までは通常表示
    assert active_notice(date(2026, 8, 2))["phase"] == "urgent"  # 締切前日
    assert active_notice(date(2026, 8, 3))["phase"] == "urgent"  # 締切当日
    assert active_notice(date(2026, 8, 4)) is None  # 締切終了後


def test_active_notice_urgent_within_same_month():
    # 2026-08 の締切は 9/1(火) → 前日 8/31 から至急（月内で切り替わるケース）
    notice = active_notice(date(2026, 8, 31))
    assert notice["target_month"] == "2026-08"
    assert notice["phase"] == "urgent"
    assert notice["deadline_label"] == "9月1日（火）"


# --- メール送信窓（due_email_notices） ---

def test_due_email_notices_windows():
    assert due_email_notices(date(2026, 7, 14)) == []
    assert due_email_notices(date(2026, 7, 15)) == [(DEADLINE_FIRST_TYPE, "2026-07")]
    assert due_email_notices(date(2026, 8, 1)) == [(DEADLINE_FIRST_TYPE, "2026-07")]  # 追い送り窓の最終日
    assert due_email_notices(date(2026, 8, 2)) == [(DEADLINE_EVE_TYPE, "2026-07")]  # 締切前日
    assert due_email_notices(date(2026, 8, 3)) == [(DEADLINE_EVE_TYPE, "2026-07")]  # 停止時の追い送り
    assert due_email_notices(date(2026, 8, 4)) == []


# --- 対象講師の抽出とメール投函 ---

def _add_tutor_with_assignment(db, email, *, active=True, system_type="legacy"):
    tutor = User(email=email, role="tutor", roles=["tutor"], display_name=email.split("@")[0], allowed_systems=["legacy"], password_hash="x")
    db.add(tutor)
    db.flush()
    assignment = Assignment(tutor_id=tutor.id, parent_id=None, student_name=f"生徒_{email}", is_active=active, system_type=system_type)
    db.add(assignment)
    db.flush()
    assignment.created_at = FIXED_CREATED_AT
    return tutor, assignment


def _add_report_row(db, assignment, status, target_month="2026-07"):
    db.add(
        LessonReport(
            assignment_id=assignment.id,
            tutor_id=assignment.tutor_id,
            parent_id=assignment.parent_id,
            lesson_date=date(2026, 7, 1),
            start_time=time(18, 0),
            end_time=time(19, 0),
            content="lesson",
            target_month=target_month,
            status=status,
        )
    )


def test_unsubmitted_tutors_selection(client, db):
    base_assignment = db.query(Assignment).first()
    base_assignment.created_at = FIXED_CREATED_AT  # 実行日に依存しないよう固定

    submitted_tutor, submitted_assignment = _add_tutor_with_assignment(db, "submitted@example.com")
    _add_report_row(db, submitted_assignment, ReportStatus.awaiting_parent_approval.value)
    draft_tutor, draft_assignment = _add_tutor_with_assignment(db, "draft@example.com")
    _add_report_row(db, draft_assignment, ReportStatus.awaiting_parent_approval.value)
    _add_report_row(db, draft_assignment, ReportStatus.draft.value)  # 一部未提出
    _add_tutor_with_assignment(db, "inactive@example.com", active=False)
    _add_tutor_with_assignment(db, "emps@example.com", system_type="new")
    db.commit()

    emails = {tutor.email for tutor in unsubmitted_tutors(db, "2026-07")}
    # 報告書未作成の講師（fixture の tutor@example.com）と draft 残りの講師のみ。
    # 提出済み・無効担当・新システム(EMPS)担当は対象外。
    assert emails == {"tutor@example.com", "draft@example.com"}


def test_deadline_mail_first_notice_queued_once(client, db, monkeypatch):
    monkeypatch.setattr(settings, "deadline_notice_enabled", True)
    db.query(Assignment).first().created_at = FIXED_CREATED_AT
    db.commit()

    assert enqueue_deadline_notices(db, date(2026, 7, 15)) == 1
    db.commit()

    outbox = db.query(MailOutbox).all()
    assert [mail.to_email for mail in outbox] == ["tutor@example.com"]
    assert outbox[0].subject == "【重要】指導報告提出締切のお知らせ"
    assert "7月分の勤務時間、日報、月報" in outbox[0].body
    assert "提出締切：8月3日（月）" in outbox[0].body
    notification = db.query(Notification).filter(Notification.type == DEADLINE_FIRST_TYPE).one()
    assert notification.sent_at is not None
    guard = db.query(DeadlineNoticeSend).one()
    assert (guard.target_month, guard.notice_type, guard.recipient_count) == ("2026-07", DEADLINE_FIRST_TYPE, 1)

    # 同月の1回目は窓内の別日でも再送しない（追い送りは未送信のときだけ）
    assert enqueue_deadline_notices(db, date(2026, 7, 16)) == 0
    db.commit()
    assert db.query(MailOutbox).count() == 1


def test_deadline_mail_eve_notice_content_and_series(client, db, monkeypatch):
    monkeypatch.setattr(settings, "deadline_notice_enabled", True)
    db.query(Assignment).first().created_at = FIXED_CREATED_AT
    db.commit()

    # 1回目（月中）→ 2回目（締切前日）は別種別として月1回ずつ送られる
    assert enqueue_deadline_notices(db, date(2026, 7, 15)) == 1
    assert enqueue_deadline_notices(db, date(2026, 8, 2)) == 1
    db.commit()

    eve_mail = db.query(MailOutbox).filter(MailOutbox.subject == "【至急確認依頼】指導報告提出締切のお知らせ").one()
    assert "7月分の指導報告について、提出締切が近づいております" in eve_mail.body
    assert "提出締切：8月3日（月）" in eve_mail.body
    assert db.query(DeadlineNoticeSend).count() == 2
    assert enqueue_deadline_notices(db, date(2026, 8, 3)) == 0  # 前日送信済みなら締切当日は再送しない


def test_deadline_mail_skips_submitted_tutor(client, db, monkeypatch):
    monkeypatch.setattr(settings, "deadline_notice_enabled", True)
    assignment = db.query(Assignment).first()
    assignment.created_at = FIXED_CREATED_AT
    _add_report_row(db, assignment, ReportStatus.awaiting_parent_approval.value)
    db.commit()

    # 全員提出済みでも送信済みガードは記録される（宛先0件）
    assert enqueue_deadline_notices(db, date(2026, 7, 15)) == 0
    db.commit()
    assert db.query(MailOutbox).count() == 0
    assert db.query(DeadlineNoticeSend).one().recipient_count == 0


def test_deadline_mail_disabled_by_default(client, db):
    db.query(Assignment).first().created_at = FIXED_CREATED_AT
    db.commit()
    # 既定は無効（誤送信防止）。投函もガード記録も行わない。
    assert settings.deadline_notice_enabled is False
    assert enqueue_deadline_notices(db, date(2026, 7, 15)) == 0
    db.commit()
    assert db.query(MailOutbox).count() == 0
    assert db.query(DeadlineNoticeSend).count() == 0


# --- 画面バナー（講師のみ・ヘッダー帯） ---

def _login(client, email):
    res = client.post("/api/auth/login", data={"username": email, "password": "Passw0rd!"})
    assert res.status_code == 200


def test_banner_shown_for_tutor_in_window(client):
    with freeze_time("2026-07-20 03:00:00"):  # JST 2026-07-20 12:00
        _login(client, "tutor@example.com")
        html = client.get("/tutor/reports").text
    assert "deadlineBanner" in html
    assert "7月分の指導報告の提出締切は、" in html
    assert "8月3日（月）" in html
    assert "提出締切が近づいています" not in html


def test_banner_switches_to_urgent_on_deadline_eve(client):
    with freeze_time("2026-08-02 03:00:00"):  # 締切(8/3)前日
        _login(client, "tutor@example.com")
        html = client.get("/tutor/reports").text
    assert "deadlineBanner" in html
    assert "【提出締切が近づいています】" in html
    assert "8月3日（月）" in html


def test_banner_hidden_outside_window(client):
    with freeze_time("2026-07-05 03:00:00"):  # 月中通知日より前
        _login(client, "tutor@example.com")
        html = client.get("/tutor/reports").text
    assert "deadlineBanner" not in html


def test_banner_not_shown_for_admin(client):
    with freeze_time("2026-07-20 03:00:00"):
        _login(client, "receiver@example.com")
        html = client.get("/admin/dashboard").text
    assert "deadlineBanner" not in html
# === 提出締切通知（改修依頼 202607161428） END ===
