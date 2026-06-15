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
