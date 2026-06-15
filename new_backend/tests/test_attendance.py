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
