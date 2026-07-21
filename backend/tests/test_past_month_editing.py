"""
過去月の指導報告を編集・提出・承認できることの検証（改修依頼 202607211716・legacy①＝案B）。

背景:
  ルーズな講師が「月をまたいでから」指導報告を入力するケースを救済するため、従来「当月のみ操作可」
  だったフロント＋バックの月ゲートを撤廃し、講師の入力可否・承認側（保護者/受付/再鑑）の操作可否を
  「報告書のステータス」で判定する（過去月＝当月と同じ扱い）。ただし未来の月は引き続き作成不可
  （UIの対象月も当月以前のみ・未実施の指導日を先取りさせないため）。

確認すること:
  1. 過去月でも 作成 → 編集(PATCH) ができる（月ゲートで弾かれない）。
  2. 過去月でも 提出(講師) → 保護者承認 → 受付 → 再鑑（最終承認）まで完走できる。
  3. 未来の月は作成不可（400）。
  4. 提出後（承認待ち以降）の過去月報告書は編集不可（月ではなくステータスで判定＝当月と同じ）。
  5. 各画面のフロント月ゲート（「過去月は参照のみ」「過去月のため承認・差戻しはできません」）が撤廃されている。
  6. テスト環境では実メールを送らない（mail_backend=console＝承認操作でもSMTP実送信なし）。
"""
from datetime import date, time

from app.core.time import get_current_jst_date
from app.models import Assignment, LessonReport, ReportStatus
from tests.conftest import seed_monthly_report, token


def _past_month_and_date(months_back: int = 2):
    """当月より過去の (target_month 'YYYY-MM', 指導日 date) を返す。"""
    today = get_current_jst_date()
    y, m = today.year, today.month
    m -= months_back
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}", date(y, m, 15)


def _future_date(months_ahead: int = 1) -> date:
    today = get_current_jst_date()
    y, m = today.year, today.month
    m += months_ahead
    while m > 12:
        m -= 12
        y += 1
    return date(y, m, 1)


def _create(client, tk, assignment, lesson_date):
    return client.post("/api/reports", headers={"Authorization": f"Bearer {tk}"}, json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(lesson_date),
        "start_time": "18:00",
        "end_time": "19:00",
        "content": "lesson",
    })


class TestPastMonthFlow:
    def test_tutor_can_create_and_edit_past_month_draft(self, client, db):
        tk = token(client, "tutor@example.com")
        assignment = db.query(Assignment).first()
        month, lesson_date = _past_month_and_date()
        res = _create(client, tk, assignment, lesson_date)
        assert res.status_code == 200, res.text
        assert res.json()["target_month"] == month
        assert res.json()["status"] == ReportStatus.draft.value
        rid = res.json()["id"]
        # 過去月の下書きを編集できる（月ゲートで弾かれない）
        res = client.patch(f"/api/reports/{rid}", headers={"Authorization": f"Bearer {tk}"},
                           json={"content": "edited", "start_time": "18:00", "end_time": "19:30"})
        assert res.status_code == 200, res.text

    def test_past_month_full_approval_flow(self, client, db):
        tk = token(client, "tutor@example.com")
        assignment = db.query(Assignment).first()
        month, lesson_date = _past_month_and_date()
        # 承認依頼には対象月の指導月報（学年＋問題点）が必須。過去月分を用意する。
        seed_monthly_report(db, assignment, target_month=month)
        rid = _create(client, tk, assignment, lesson_date).json()["id"]
        steps = [
            ("tutor@example.com", "submit-to-parent", "awaiting_parent_approval"),
            ("parent@example.com", "parent-approve", "submitted_to_admin"),
            ("receiver@example.com", "receive", "received"),
            ("reviewer@example.com", "re-review", "admin_approved"),
        ]
        for email, endpoint, status in steps:
            res = client.post(f"/api/reports/{rid}/{endpoint}",
                              headers={"Authorization": f"Bearer {token(client, email)}"}, json={})
            assert res.status_code == 200, f"{email}/{endpoint}: {res.text}"
            assert res.json()["status"] == status

    def test_future_month_still_rejected(self, client, db):
        tk = token(client, "tutor@example.com")
        assignment = db.query(Assignment).first()
        res = _create(client, tk, assignment, _future_date())
        assert res.status_code == 400, res.text
        assert res.json()["detail"] == "未来の月の報告書は作成できません"

    def test_submitted_past_month_report_not_editable(self, client, db):
        # 提出後（承認待ち）の過去月報告書は編集不可（月ではなくステータスで判定＝当月と同じ挙動）。
        assignment = db.query(Assignment).first()
        month, lesson_date = _past_month_and_date()
        report = LessonReport(
            assignment_id=assignment.id, tutor_id=assignment.tutor_id, parent_id=assignment.parent_id,
            lesson_date=lesson_date, start_time=time(18, 0), end_time=time(19, 0), break_minutes=0,
            content="submitted", target_month=month, status=ReportStatus.awaiting_parent_approval.value,
        )
        db.add(report)
        db.commit()
        res = client.patch(f"/api/reports/{report.id}",
                           headers={"Authorization": f"Bearer {token(client, 'tutor@example.com')}"},
                           json={"content": "try edit"})
        assert res.status_code == 409, res.text


class TestPastMonthFrontendGates:
    """フロントの当月ゲートが撤廃されていること（テンプレートに旧ゲート文言が残っていない）。"""

    def test_tutor_reports_has_no_past_month_readonly_gate(self, client, db):
        token(client, "tutor@example.com")
        html = client.get("/tutor/reports").text
        assert 'id="monthFilter"' in html  # 報告書一覧ページが取得できている前提
        assert "過去月は参照のみです" not in html

    def test_parent_approval_has_no_past_month_gate(self, client, db):
        token(client, "parent@example.com")
        html = client.get("/parent/approval").text
        assert "過去月のため承認・差戻しはできません" not in html

    def test_parent_report_view_has_no_past_month_gate(self, client, db):
        assignment = db.query(Assignment).first()
        month, _ = _past_month_and_date()
        token(client, "parent@example.com")
        html = client.get(f"/parent/report-view?assignment_id={assignment.id}&month={month}").text
        assert "REQUEST_BALL_STATUSES" in html  # report_view.html が描画されている前提
        assert "過去月のため承認・差戻しはできません" not in html


class TestNoRealMail:
    def test_environment_sends_no_real_mail(self):
        """テスト環境は mail_backend!=smtp 固定＝承認等でSMTP実送信は起きない（本番メール誤送信の防止）。"""
        from app.config import settings
        assert (settings.mail_backend or "console").lower() != "smtp"
