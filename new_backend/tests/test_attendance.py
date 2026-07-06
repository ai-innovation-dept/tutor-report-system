"""勤怠区分（有給休暇・欠勤）の集計とPDF反映のテスト。"""
import pytest

from app.services.report_service import attendance_counts, is_leave_kind


def test_attendance_counts_basic():
    lines = [
        {"kind": "", "date": "2026-06-01", "teach_minutes": 60},
        {"kind": "paid_leave", "date": "2026-06-02"},
        {"kind": "absent", "date": "2026-06-03"},
        {"kind": "paid_leave", "date": "2026-06-04"},
        {"kind": "", "date": "", "teach_minutes": ""},  # 空行は勤務日数に含めない
    ]
    assert attendance_counts(lines) == {"paid_leave": 2, "absent": 1, "work_days": 1}


def test_attendance_counts_missing_kind_is_work():
    lines = [
        {"date": "2026-06-01", "teach_minutes": 30},
        {"date": "2026-06-02", "teach_minutes": 30},
    ]
    counts = attendance_counts(lines)
    assert counts["work_days"] == 2
    assert counts["paid_leave"] == 0
    assert counts["absent"] == 0


def test_attendance_counts_handles_non_dict_and_empty():
    assert attendance_counts([]) == {"paid_leave": 0, "absent": 0, "work_days": 0}
    assert attendance_counts(None) == {"paid_leave": 0, "absent": 0, "work_days": 0}
    assert attendance_counts(["x", None, {"kind": "absent", "date": "2026-06-03"}])["absent"] == 1


def test_is_leave_kind():
    assert is_leave_kind("paid_leave")
    assert is_leave_kind("absent")
    assert not is_leave_kind("")
    assert not is_leave_kind("work")
    assert not is_leave_kind(None)


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
    assert "勤務日数：2日" in joined  # 有給1日・未記入1行は勤務日数に含めない
    assert "有給休暇：1回" in joined
    assert "数学指導（分）：150" in joined  # 90 + 60
    assert "英語補習（分）：30" in joined   # 30 + 0
    assert "採点（回）：5回 / 30分" in joined
    assert "往復交通費（円）：1,000" in joined  # 500 + 500


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
    """PDFヘッダーは参照画面と同じ対象月・学校/講師番号・契約情報を持つ。"""
    from app.models.shared import Assignment, User
    from app.services.export_service import _report_header_values

    report = _dynamic_report()
    school = User(display_name="渋谷高校", user_no="40001")
    tutor = User(display_name="山田太郎", user_no="10002", tutor_no="10002")
    report.assignment = Assignment(student_name="旧表示名", parent=school)
    report.tutor = tutor

    title, fields = _report_header_values(report, "渋谷高校", "山田太郎")

    assert title == "2026年6月分 業務連絡表"
    assert fields == [
        ("学校名", "渋谷高校（40001）"),
        ("講師名", "山田太郎（10002）"),
        ("弊社担当", "山田"),
        ("事業所の所在地", "渋谷区1-1"),
        ("従事業務内容", "数学指導"),
    ]


def test_pdf_header_grid_matches_report_view_layout():
    """PDFヘッダーの2列グリッドは参照画面と同じ並び（学校名|講師名 / 弊社担当|所在地 / 従事業務内容）。"""
    from app.services.export_service import _header_grid_rows

    fields = [
        ("学校名", "渋谷高校（40001）"),
        ("講師名", "山田太郎（10002）"),
        ("弊社担当", "山田"),
        ("事業所の所在地", "渋谷区1-1"),
        ("従事業務内容", "数学指導"),
    ]
    assert _header_grid_rows(fields) == [
        [("学校名", "渋谷高校（40001）"), ("講師名", "山田太郎（10002）")],
        [("弊社担当", "山田"), ("事業所の所在地", "渋谷区1-1")],
        [("従事業務内容", "数学指導")],
    ]


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
    assert len(rows) == 3  # ヘッダ + 勤務日 + 有給日（未記入行は除外）
    assert "T1001,大橋悟史,渋谷高校,C001,2026-06,2026-06-01," in text
    assert "数学指導,90" in text
    assert "英語補習,30" in text
    assert "採点,5,30" in text
    assert "有給休暇" in text
