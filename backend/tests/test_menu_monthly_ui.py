# === 改修 202607231755 の画面文字列テスト START ===
"""改修 202607231755 のUI検証（ページHTMLの文字列レベル）。

- ② 講師メニュー「報告書一覧」→「指導報告・日報」への改称（サイドバー・パンくず）
- ① 月報作成の志望校引継ぎの案内文言（テンプレートに存在すること）
- ④ 月報の必須は「次月に向けての問題点と対策」のみ（学年は※任意表記へ）
- ③ 保護者の参照画面に講師評価アンケート欄（運営のみ閲覧の注記つき）があること
"""
from tests.conftest import token


def test_tutor_menu_renamed_to_shido_hokoku_nippo(client, db):
    token(client, "tutor@example.com")
    html = client.get("/tutor/reports").text
    assert "navLink('/tutor/reports', '指導報告・日報')" in html
    assert "navLink('/tutor/reports', '報告書一覧')" not in html
    assert "setBreadcrumb('講師ポータル', '指導報告・日報');" in html


def test_monthly_report_page_grade_optional_and_target_school_inherit(client, db):
    token(client, "tutor@example.com")
    html = client.get("/tutor/monthly-report").text
    # ④ 学年は任意表記・必須案内は問題点と対策のみ（学年の旧・必須マーカーが残っていない）
    assert '学年 <span class="font-medium">※任意</span>' in html
    assert 'text-amber-600">※必須' not in html
    assert "承認依頼に必須なのは" in html
    # ① 志望校の引継ぎ案内（月報未作成時のデフォルト表示）
    assert "前回の月報の内容を引継ぎ表示しています" in html
    assert "previous_target_schools" in html


def test_parent_report_view_has_survey_section(client, db):
    token(client, "parent@example.com")
    html = client.get("/parent/report-view?month=2026-07").text
    assert "講師評価アンケート" in html
    assert "講師には表示されません" in html
    assert "/api/parent-surveys/" in html
# === 改修 202607231755 の画面文字列テスト END ===
