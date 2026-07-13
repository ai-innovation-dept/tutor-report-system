# === 指導時間の計算規約（在室時間・指導時間数15分切り捨て） START ===
from datetime import time
from types import SimpleNamespace

import pytest

from app.api.reports import _hours_label
from app.services.lesson_time import duration_label, presence_minutes, teaching_minutes


def _report(start: str, end: str, break_minutes: int = 0) -> SimpleNamespace:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return SimpleNamespace(start_time=time(sh, sm), end_time=time(eh, em), break_minutes=break_minutes)


def test_presence_minutes_is_end_minus_start_minus_break():
    """在室時間＝終了−開始−休憩（1分単位）"""
    assert presence_minutes(_report("17:00", "18:20", 5)) == 75
    assert presence_minutes(_report("17:03", "18:19", 4)) == 72
    assert presence_minutes(_report("09:00", "10:00", 0)) == 60


def test_presence_minutes_clamps_to_zero():
    """休憩が在室の全時間以上でも負値にならない（0に丸め）"""
    assert presence_minutes(_report("17:00", "17:30", 40)) == 0
    assert presence_minutes(_report("17:00", "17:30", 30)) == 0


@pytest.mark.parametrize(
    ("presence", "expected"),
    [
        (74, 60),   # 1時間14分 → 1時間00分
        (75, 75),   # 1時間15分 → 1時間15分
        (76, 75),   # 1時間16分 → 1時間15分
        (89, 75),   # 1時間29分 → 1時間15分
        (90, 90),   # 1時間30分 → 1時間30分
        (14, 0),    # 15分未満 → 0分
        (0, 0),
    ],
)
def test_teaching_minutes_floors_presence_to_15min_unit(presence, expected):
    """指導時間数＝在室時間の15分単位切り捨て（開始・終了時刻そのものは丸めない）"""
    assert teaching_minutes(_report("17:00", f"{17 + presence // 60}:{presence % 60:02d}", 0)) == expected


def test_teaching_minutes_uses_presence_not_raw_times():
    """休憩を引いた在室時間に対して切り捨てる（例：在室2時間10分−休憩10分＝2時間→2時間）"""
    assert teaching_minutes(_report("17:00", "19:10", 10)) == 120
    # 在室80分（休憩5分）→ 75分
    assert teaching_minutes(_report("17:00", "18:25", 5)) == 75


def test_duration_label():
    assert duration_label(90) == "1時間30分"
    assert duration_label(60) == "1時間"
    assert duration_label(45) == "45分"
    assert duration_label(0) == "0分"


def test_hours_label_shows_exact_quarter_hours():
    """確認票PDFの時間数表示＝15分（0.25時間）単位の正確な表示（四捨五入しない）"""
    assert _hours_label(120) == "2"
    assert _hours_label(150) == "2.5"
    assert _hours_label(75) == "1.25"
    assert _hours_label(105) == "1.75"
    assert _hours_label(0) == "0"
# === 指導時間の計算規約（在室時間・指導時間数15分切り捨て） END ===
