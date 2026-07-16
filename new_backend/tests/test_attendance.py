"""勤怠区分（有給休暇・欠勤・自己都合・学校行事）の集計とPDF反映のテスト。"""
import pytest

from app.services.report_service import attendance_counts, is_leave_kind, is_no_main_duty_kind

_EMPTY_COUNTS = {"paid_leave": 0, "absent": 0, "personal_reason": 0, "school_event": 0, "work_days": 0}


def test_attendance_counts_basic():
    lines = [
        {"kind": "", "date": "2026-06-01", "teach_minutes": 60},
        {"kind": "paid_leave", "date": "2026-06-02"},
        {"kind": "absent", "date": "2026-06-03"},
        {"kind": "paid_leave", "date": "2026-06-04"},
        {"kind": "personal_reason", "date": "2026-06-05"},
        {"kind": "school_event", "date": "2026-06-06", "sub_minutes_1": 60},
        {"kind": "", "date": "", "teach_minutes": ""},  # 空行は勤務日数に含めない
    ]
    assert attendance_counts(lines) == {
        "paid_leave": 2, "absent": 1, "personal_reason": 1, "school_event": 1, "work_days": 1,
    }


def test_attendance_counts_missing_kind_is_work():
    lines = [
        {"date": "2026-06-01", "teach_minutes": 30},
        {"date": "2026-06-02", "teach_minutes": 30},
    ]
    counts = attendance_counts(lines)
    assert counts["work_days"] == 2
    assert counts["paid_leave"] == 0
    assert counts["absent"] == 0
    assert counts["personal_reason"] == 0
    assert counts["school_event"] == 0


def test_attendance_counts_handles_non_dict_and_empty():
    assert attendance_counts([]) == _EMPTY_COUNTS
    assert attendance_counts(None) == _EMPTY_COUNTS
    assert attendance_counts(["x", None, {"kind": "absent", "date": "2026-06-03"}])["absent"] == 1


def test_is_leave_kind():
    assert is_leave_kind("paid_leave")
    assert is_leave_kind("absent")
    assert not is_leave_kind("")
    assert not is_leave_kind("work")
    assert not is_leave_kind(None)
    assert not is_leave_kind("personal_reason")
    assert not is_leave_kind("school_event")


def test_is_no_main_duty_kind():
    assert is_no_main_duty_kind("personal_reason")
    assert is_no_main_duty_kind("school_event")
    assert not is_no_main_duty_kind("")
    assert not is_no_main_duty_kind("work")
    assert not is_no_main_duty_kind("paid_leave")
    assert not is_no_main_duty_kind("absent")
    assert not is_no_main_duty_kind(None)


def test_build_report_pdf_includes_attendance_rows():
    """有給/欠勤を含む報告書のPDFがエラーなく生成できる（フォント未導入環境はスキップ）。"""
    from app.models.work import WorkReport
    from app.services.export_service import build_report_pdf

    report = WorkReport(
        target_month="2026-06",
        form_type="monthly_dispatch",
        form_data={"lines": [
            {"kind": "", "date": "2026-06-01", "start": "09:00", "end": "10:00", "teach_minutes": 60},
            {"kind": "paid_leave", "date": "2026-06-02"},
            {"kind": "absent", "date": "2026-06-03"},
        ]},
        status="approved",
    )
    try:
        pdf = build_report_pdf(report, "テスト学校", "テスト講師")
    except RuntimeError as exc:
        if "font" in str(exc).lower():
            pytest.skip("Japanese PDF font not available in this environment")
        raise
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def _dynamic_report():
    """動的列（担当業務・副業務・採点）の列定義スナップショットを持つ報告書。"""
    from app.models.work import WorkReport

    return WorkReport(
        target_month="2026-06", form_type="monthly_dispatch", status="approved",
        form_data={
            "meta": {
                "our_staff": "山田", "dispatch_place_address": "渋谷区1-1",
                "work_content": "数学指導",
                "column_definition": [
                    {"key": "date", "label": "日付", "type": "date", "summable": False},
                    {"key": "start", "label": "業務開始時間", "type": "time", "summable": False},
                    {"key": "end", "label": "業務終了時間", "type": "time", "summable": False},
                    {"key": "subject_period", "label": "担当時限", "type": "number", "summable": False},
                    {"key": "task_minutes_1", "label": "数学指導（分）", "type": "number", "summable": True},
                    {"key": "sub_minutes_1", "label": "英語補習（分）", "type": "number", "summable": True},
                    {"key": "scoring", "label": "採点（回）", "type": "count_minutes", "summable": True,
                     "unit": "回", "count_key": "scoring_count", "minutes_key": "scoring_minutes"},
                    {"key": "break_minutes", "label": "休憩時間（分）", "type": "number", "summable": True},
                    {"key": "commute_fee", "label": "往復交通費（円）", "type": "number", "summable": True},
                    {"key": "note", "label": "内容", "type": "text", "summable": False},
                ],
            },
            "lines": [
                {"date": "2026-06-01", "kind": "", "start": "09:00", "end": "10:30",
                 "subject_period": "1・2", "task_minutes_1": 90, "sub_minutes_1": 30,
                 "scoring_count": 5, "scoring_minutes": 30, "break_minutes": 10,
                 "commute_fee": 500, "note": "メモ"},
                {"date": "2026-06-02", "kind": "", "start": "09:00", "end": "10:00",
                 "task_minutes_1": 60, "sub_minutes_1": 0, "scoring_count": 0,
                 "scoring_minutes": 0, "break_minutes": 0, "commute_fee": 500, "note": ""},
                {"date": "2026-06-03", "kind": "paid_leave"},
                # 学校行事: 担当業務0固定・副業務等は入力可（分は合計に含め、勤務日数には含めない）
                {"date": "2026-06-04", "kind": "school_event", "task_minutes_1": 0,
                 "sub_minutes_1": 40, "commute_fee": 500},
                {"date": "", "kind": ""},  # 未記入行 → 除外
            ],
        },
    )


def test_pdf_display_columns_follow_snapshot():
    """PDFの表示列が静的フォームではなく保存済みスナップショット列定義に従う（②修正）。"""
    from app.services.export_service import _display_columns

    cols = _display_columns(_dynamic_report())
    keys = [c.get("key") for c in cols]
    # 動的列（担当業務・副業務・採点）が出力に含まれる。静的の teach_minutes は含まれない。
    assert "task_minutes_1" in keys
    assert "sub_minutes_1" in keys
    assert "scoring" in keys
    assert "teach_minutes" not in keys
    # 種別は日付の直後、開始/終了は1列(業務開始〜終了時間)へ結合される（参照ビューと同一）。
    assert keys[0] == "date" and keys[1] == "kind"
    assert "__timerange" in keys and "start" not in keys and "end" not in keys


def test_pdf_summary_sums_dynamic_columns():
    """サマリが勤務行のみで動的な集計可能列を合計する（参照ビューと同一）。"""
    from app.services.export_service import _summary_parts

    report = _dynamic_report()
    parts = _summary_parts(report, report.form_data["lines"])
    joined = "　".join(parts)
    assert "勤務日数：2日" in joined  # 有給1日・学校行事1日・未記入1行は勤務日数に含めない
    assert "有給休暇：1回" in joined
    assert "自己都合：0回" in joined
    assert "学校行事：1回" in joined
    assert "数学指導（分）：150" in joined  # 90 + 60 + 0（学校行事は0固定）
    assert "英語補習（分）：70" in joined   # 30 + 0 + 40（学校行事の副業務も合計に含める）
    assert "採点（回）：5回 / 30分" in joined
    assert "往復交通費（円）：1,500" in joined  # 500 + 500 + 500（学校行事の交通費も含める）


def test_build_report_pdf_dynamic_columns_smoke():
    """動的列を持つ報告書のPDFがエラーなく生成できる（フォント未導入環境はスキップ）。"""
    from app.services.export_service import build_report_pdf

    try:
        pdf = build_report_pdf(_dynamic_report(), "渋谷高校", "テスト講師")
    except RuntimeError as exc:
        if "font" in str(exc).lower():
            pytest.skip("Japanese PDF font not available in this environment")
        raise
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_pdf_header_matches_report_view():
    """PDFヘッダーは参照画面と同じく講師フォームのヘッダー項目（名称・組織単位〜従事業務内容）を漏れなく持つ。"""
    from app.models.shared import Assignment, User
    from app.services.export_service import _report_header_values

    report = _dynamic_report()
    report.form_data["meta"]["classroom_name"] = "第2教室"
    report.form_data["meta"]["customer_id"] = "C-100"
    report.form_data["meta"]["work_location"] = "〇〇高等学校 △△校舎"
    school = User(display_name="渋谷高校", user_no="40001")
    tutor = User(display_name="山田太郎", user_no="10002", tutor_no="10002")
    report.assignment = Assignment(student_name="旧表示名", parent=school)
    report.tutor = tutor

    title, fields = _report_header_values(report, "渋谷高校", "山田太郎")

    assert title == "2026年6月分 業務連絡表"
    assert fields == [
        ("事業所の名称・組織単位", "渋谷高校（40001）"),
        ("氏名", "山田太郎"),
        ("教室名", "第2教室"),
        ("講師番号", "10002"),
        ("事業所の所在地", "渋谷区1-1"),
        ("お客様ID", "C-100"),
        ("就業場所", "〇〇高等学校 △△校舎"),
        ("", ""),
        ("従事業務内容", "数学指導"),
    ]


def test_pdf_header_grid_matches_report_view_layout():
    """PDFヘッダーの2列グリッドは参照画面と同じ並び（左列＝事業所情報／右列＝講師情報）。"""
    from app.services.export_service import _header_grid_rows

    fields = [
        ("事業所の名称・組織単位", "渋谷高校（40001）"),
        ("氏名", "山田太郎"),
        ("教室名", "第2教室"),
        ("講師番号", "10002"),
        ("事業所の所在地", "渋谷区1-1"),
        ("お客様ID", "C-100"),
        ("就業場所", "〇〇高等学校 △△校舎"),
        ("", ""),
        ("従事業務内容", "数学指導"),
    ]
    assert _header_grid_rows(fields) == [
        [("事業所の名称・組織単位", "渋谷高校（40001）"), ("氏名", "山田太郎")],
        [("教室名", "第2教室"), ("講師番号", "10002")],
        [("事業所の所在地", "渋谷区1-1"), ("お客様ID", "C-100")],
        # 就業場所は「事業所の所在地」の直下（左列）。右列は空欄。
        [("就業場所", "〇〇高等学校 △△校舎"), ("", "")],
        [("従事業務内容", "数学指導")],
    ]


def test_pdf_footer_includes_all_form_items():
    """PDFフッターは講師フォームの明細より下の項目（弊社担当〜定期代）を漏れなく持つ。"""
    from app.services.export_service import _report_footer_values

    report = _dynamic_report()
    report.form_data["meta"].update({
        "note_schedule": "毎週 月・水",
        "requests": "数学指導：（月　1,200　分固定）\n契約期間：2026年04月01日　～　2027年03月31日",
        "commuter_pass_months": "3",
        "commuter_pass_amount": "12000",
        "commuter_pass_route": "渋谷〜新宿",
        "commuter_pass_purchase_date": "2026-06-01",
        "commuter_pass_from": "2026-06-01",
        "commuter_pass_to": "2026-08-31",
    })

    fields = dict(_report_footer_values(report))

    assert fields["弊社担当"] == "山田"
    # 委託業務は列定義スナップショットから導出（担当→副→採点の順）
    assert fields["委託業務（契約より）"] == "【担当】数学指導（分）\n【副】英語補習（分）\n採点（回）"
    assert fields["スケジュール欄"] == "毎週 月・水"
    assert "契約期間：2026年04月01日" in fields["要望連絡事項"]
    commuter = fields["定期代（購入時のみ記入）"]
    assert "期間選択・金額：3ヶ月 / 12,000円" in commuter
    assert "区間（経路）：渋谷〜新宿" in commuter
    assert "購入日：2026年06月01日" in commuter
    assert "期間（from〜to）：2026年06月01日 〜 2026年08月31日" in commuter


def test_pdf_footer_prefers_task_reference_snapshot():
    """meta.task_reference（前期・後期のスナップショット）があれば列定義由来より優先する。"""
    from app.services.export_service import _report_footer_values

    report = _dynamic_report()
    snapshot = (
        "【前期】数学指導（分） / 委託業務ID:T1 / 個別契約ID:C1\n"
        "【後期】数学指導（後期）（分） / 委託業務ID:T2 / 個別契約ID:C2\n"
        "【副】英語補習（分）"
    )
    report.form_data["meta"]["task_reference"] = snapshot

    fields = dict(_report_footer_values(report))
    assert fields["委託業務（契約より）"] == snapshot


def test_pdf_footer_empty_commuter_pass_and_defaults():
    """定期代が全項目未記入なら「記入なし」、未入力メタは「-」で出力する。"""
    from app.models.work import WorkReport
    from app.services.export_service import _report_footer_values

    report = WorkReport(
        target_month="2026-06", form_type="monthly_dispatch", status="approved",
        form_data={"lines": []},
    )
    fields = dict(_report_footer_values(report))

    assert fields["定期代（購入時のみ記入）"] == "記入なし"
    assert fields["スケジュール欄"] == "-"
    assert fields["要望連絡事項"] == "-"
    # 旧データ（契約由来の列が無い）は委託業務も「-」
    assert fields["委託業務（契約より）"] == "-"


def test_build_report_pdf_with_footer_smoke():
    """フッター（弊社担当〜定期代）込みのPDFがエラーなく生成できる（フォント未導入環境はスキップ）。"""
    from app.services.export_service import build_report_pdf

    report = _dynamic_report()
    report.form_data["meta"].update({
        "requests": "要望です",
        "commuter_pass_months": "1",
        "commuter_pass_amount": "5000",
    })
    try:
        pdf = build_report_pdf(report, "渋谷高校", "テスト講師")
    except RuntimeError as exc:
        if "font" in str(exc).lower():
            pytest.skip("Japanese PDF font not available in this environment")
        raise
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1000


def test_build_reports_csv_positional_wide():
    """全講師分CSV（横持ち・位置固定）: 列固定・業務名はセル値・有給日も1行・未記入行は除外。"""
    from app.models.shared import User
    from app.models.work import WorkReport
    from app.services.export_service import build_reports_csv

    tutor = User(email="t@e.com", role="tutor", display_name="大橋悟史",
                 user_no="T1001", password_hash="x")
    report = WorkReport(
        target_month="2026-06", form_type="monthly_dispatch", status="approved",
        form_data={
            "meta": {
                "customer_id": "C001", "dispatch_place_name": "渋谷高校",
                "column_definition": [
                    {"key": "task_minutes_1", "label": "数学指導（分）", "type": "number"},
                    {"key": "sub_minutes_1", "label": "英語補習（分）", "type": "number"},
                    {"key": "scoring", "type": "count_minutes", "label": "採点（回）",
                     "count_key": "scoring_count", "minutes_key": "scoring_minutes"},
                ],
            },
            "lines": [
                {"date": "2026-06-01", "kind": "", "start": "09:00", "end": "10:30",
                 "subject_period": "1・2", "task_minutes_1": 90, "sub_minutes_1": 30,
                 "scoring_count": 5, "scoring_minutes": 30, "break_minutes": 10,
                 "commute_fee": 500, "note": "メモ"},
                {"date": "2026-06-03", "kind": "paid_leave"},
                {"date": "2026-06-04", "kind": "personal_reason", "task_minutes_1": 0, "sub_minutes_1": 30},
                {"date": "", "kind": ""},  # 未記入行 → 除外
            ],
        },
    )
    report.tutor = tutor
    report.assignment = None
    text = build_reports_csv([report], "2026-06").decode("utf-8-sig")
    rows = [line for line in text.splitlines() if line]
    assert rows[0].startswith("講師番号,講師名,派遣先,お客様ID,対象月,日付,曜日,種別,業務開始,業務終了,担当時限")
    assert "担当業務1_名称" in rows[0] and "副業務5_分" in rows[0] and "採点_回数" in rows[0]
    assert len(rows) == 4  # ヘッダ + 勤務日 + 有給日 + 自己都合日（未記入行は除外）
    assert "T1001,大橋悟史,渋谷高校,C001,2026-06,2026-06-01," in text
    assert "数学指導,90" in text
    assert "英語補習,30" in text
    assert "採点,5,30" in text
    assert "有給休暇" in text
    assert "自己都合" in text
