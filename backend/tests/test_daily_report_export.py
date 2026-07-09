# === 指導日報PDF（/api/reports/export-daily） START ===
import re
from datetime import date, datetime, time, timezone
from urllib.parse import quote

from app.api import reports as reports_api
from app.core.security import hash_password
from app.models import Assignment, LessonReport, ReportStatus, User
from app.services.daily_report_pdf import _has_member_stamp, build_daily_reports_pdf
from tests.conftest import token

TARGET_MONTH = "2026-07"
APPROVED_AT = datetime(2026, 7, 31, 5, 0, tzinfo=timezone.utc)


def _make_report(db, assignment, day, status=ReportStatus.admin_approved.value, **kw):
    report = LessonReport(
        assignment_id=assignment.id,
        tutor_id=assignment.tutor_id,
        parent_id=assignment.parent_id,
        lesson_date=date(2026, 7, day),
        start_time=kw.get("start_time", time(17, 0)),
        end_time=kw.get("end_time", time(19, 0)),
        break_minutes=kw.get("break_minutes", 10),
        grade_level=kw.get("grade_level", "中"),
        grade_year=kw.get("grade_year", 3),
        subject=kw.get("subject", "数学"),
        content=kw.get("content", "二次方程式の解の公式"),
        material_name=kw.get("material_name", "ニューコース中3数学"),
        learning_status=kw.get("learning_status", "計算は正確。文章題を継続演習。"),
        homework_status=kw.get("homework_status", "A"),
        next_homework=kw.get("next_homework", "P.42〜45の練習問題"),
        next_lesson_date=kw.get("next_lesson_date", date(2026, 7, day + 7) if day + 7 <= 31 else None),
        next_lesson_start=kw.get("next_lesson_start", time(17, 0)),
        target_month=TARGET_MONTH,
        status=status,
        parent_approved_at=kw.get("parent_approved_at", APPROVED_AT),
    )
    db.add(report)
    return report


def _page_count(content: bytes) -> int:
    match = re.search(rb"/Count (\d+)", content)
    assert match, "PDF page tree not found"
    return int(match.group(1))


def test_daily_export_tutor_own_approved_month(client, db):
    """講師は自分の最終承認済み月の指導日報PDFをダウンロードできる（実ビルダー）"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    for day in (1, 8, 15):
        _make_report(db, assignment, day)
    db.commit()

    res = client.get(
        f"/api/reports/export-daily?target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {tutor_token}"},
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    # ファイル名は仕様どおり 指導日報_yyyy年mm月.pdf 固定
    assert quote("指導日報_2026年07月.pdf") in res.headers["content-disposition"]
    # 3日分は1ページ（1ページ5日分）
    assert _page_count(res.content) == 1


def test_daily_export_paginates_five_frames_per_page(client, db):
    """6日分以上は5日ごとに改ページされる"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    for day in (1, 3, 8, 10, 15, 17, 22):
        _make_report(db, assignment, day)
    db.commit()

    res = client.get(
        f"/api/reports/export-daily?target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {tutor_token}"},
    )
    assert res.status_code == 200
    assert _page_count(res.content) == 2


def test_daily_export_tutor_excludes_unapproved(client, db):
    """講師は最終承認前の報告書を指導日報としてダウンロードできない"""
    tutor_token = token(client, "tutor@example.com")
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, 1, status=ReportStatus.received.value)
    db.commit()

    res = client.get(
        f"/api/reports/export-daily?target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {tutor_token}"},
    )
    assert res.status_code == 404


def test_daily_export_tutor_cannot_export_other_tutor(client, db):
    tutor_token = token(client, "tutor@example.com")
    second_tutor = User(
        email="tutor2@example.com",
        role="tutor",
        roles=["tutor"],
        display_name="Tutor 2",
        password_hash=hash_password("Passw0rd!"),
    )
    db.add(second_tutor)
    db.commit()

    res = client.get(
        f"/api/reports/export-daily?tutor_id={second_tutor.id}&target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {tutor_token}"},
    )
    assert res.status_code == 403


def test_daily_export_parent_scope_all(client, db, monkeypatch):
    """保護者は自分の子の最終承認済み分をダウンロードできる（対象選定は/exportと同一）"""
    captured = {}

    def fake_pdf(reports, target_month):
        captured["reports"] = reports
        return b"%PDF-1.4\ndaily\n"

    monkeypatch.setattr(reports_api, "build_daily_reports_pdf", fake_pdf)
    parent_token = token(client, "parent@example.com")
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, 1)
    _make_report(db, assignment, 8, status=ReportStatus.received.value)
    db.commit()

    res = client.get(
        f"/api/reports/export-daily?scope=all&target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {parent_token}"},
    )
    assert res.status_code == 200
    assert res.content.startswith(b"%PDF")
    assert [r.status for r in captured["reports"]] == [ReportStatus.admin_approved.value]


def test_daily_export_admin_scope_approved_only(client, db, monkeypatch):
    captured = {}

    def fake_pdf(reports, target_month):
        captured["reports"] = reports
        return b"%PDF-1.4\ndaily\n"

    monkeypatch.setattr(reports_api, "build_daily_reports_pdf", fake_pdf)
    master_token = token(client, "master@example.com")
    assignment = db.query(Assignment).first()
    _make_report(db, assignment, 1)
    _make_report(db, assignment, 8, status=ReportStatus.received.value)
    db.commit()

    res = client.get(
        f"/api/reports/export-daily?scope=approved_only&target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert res.status_code == 200
    assert all(r.status == ReportStatus.admin_approved.value for r in captured["reports"])


def test_daily_export_rejects_invalid_scope(client, db):
    master_token = token(client, "master@example.com")
    res = client.get(
        f"/api/reports/export-daily?scope=bad&target_month={TARGET_MONTH}",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert res.status_code == 422


def test_build_daily_pdf_handles_missing_optional_fields(client, db):
    """任意項目（学年・教材・宿題状況・次回予定など）が未入力でも生成できる"""
    assignment = db.query(Assignment).first()
    report = _make_report(
        db, assignment, 1,
        grade_level=None, grade_year=None, subject=None,
        material_name=None, learning_status=None, homework_status=None,
        next_homework=None, next_lesson_date=None, next_lesson_start=None,
        parent_approved_at=None, status=ReportStatus.received.value,
    )
    db.commit()
    db.refresh(report)
    content = build_daily_reports_pdf([report], TARGET_MONTH)
    assert content.startswith(b"%PDF")
    assert _page_count(content) == 1


def test_member_stamp_visibility_rule(client, db):
    """会員認め印は「保護者承認済み かつ 承認が有効な状態」でのみ描画対象になる"""
    assignment = db.query(Assignment).first()
    approved = _make_report(db, assignment, 1, status=ReportStatus.admin_approved.value)
    in_flow = _make_report(db, assignment, 3, status=ReportStatus.submitted_to_admin.value)
    returned = _make_report(db, assignment, 8, status=ReportStatus.returned_to_tutor.value)
    closed = _make_report(db, assignment, 15, status=ReportStatus.closed.value)
    not_approved = _make_report(db, assignment, 22, status=ReportStatus.awaiting_parent_approval.value, parent_approved_at=None)
    db.commit()

    assert _has_member_stamp(approved) is True
    assert _has_member_stamp(in_flow) is True
    assert _has_member_stamp(returned) is False  # 差戻し中は承認が無効
    assert _has_member_stamp(closed) is False  # 無効クローズは押印しない
    assert _has_member_stamp(not_approved) is False
# === 指導日報PDF（/api/reports/export-daily） END ===
