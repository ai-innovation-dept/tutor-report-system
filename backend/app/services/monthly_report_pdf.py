# === 指導月報PDF START ===
"""「指導月報」PDFの生成（原本: docs/イスト勤怠レポート for 代々木進学会/原本_月報.pdf）。

紙の指導月報（右面=報告用（小学生））を忠実に再現し、指導月報（MonthlyReport 1件＝
担当×対象月）を1ページに流し込む。保護者（会員）承認を通過した月の会員認め印欄には
電子印（朱色）を描画する（判定は指導日報PDFと同一ルールを共用）。

座標系は原本PDF（見開き版下・トンボ付き）の右面の座標をそのまま使い、仕上がり枠
（センタートンボ実測: x中心1137.4 / y 131.0〜1008.9・幅631.28は日報と同一の用紙）を
A4縦へ線形変換して描画する。原本から実測した座標を変更する場合は docs の原本PDFと
突き合わせること。文字系ヘルパー・認め印判定は daily_report_pdf.py と共用する。
"""
import io
from datetime import date

from app.services.daily_report_pdf import (
    _baseline,
    _draw_block,
    _draw_fitted,
    _fit_line,
    _has_member_stamp,
    _to_jst,
)
from app.services.monthly_report_service import MOCK_SUBJECTS, SCHOOL_SUBJECTS, normalize_form_data
from app.services.pdf_fonts import register_pdf_font

# 原本の仕上がり枠（トンボ実測・右面）。この範囲をA4(595.28x841.89pt)全面へ写像する。
_SRC_X0, _SRC_Y0 = 821.76, 131.0
_SRC_W, _SRC_H = 631.28, 877.90

# 原本の色（リッチブラック／罫線・文字）、見出しバーのグレー、外周グレー、認め印プレースホルダの淡灰
_INK = (0.135, 0.094, 0.082)
_BAR_GRAY = (0.864, 0.866, 0.868)
_FRAME_GRAY = (0.5, 0.499, 0.5)
_PLACEHOLDER_GRAY = (0.79, 0.79, 0.79)
_STAMP_RED = "#c81e1e"

_WD = ["日", "月", "火", "水", "木", "金", "土"]

_HEAD_NOTES = [
    "■空欄のないように全て記入すること。(鉛筆不可)",
    "■授業終了後に記入すること（授業中に作成しないこと）",
    "■会員認め㊞が漏れている場合は、会での受付が出来ませんのでご注意ください。",
    "■1枚目が講師用、2枚目がご家庭控えとなります。",
]
_COMPANY_FOOTER = (
    "株式会社イスト　〒151-0053 東京都渋谷区代々木1-35-4 代々木クリスタルビル5階 "
    "TEL:03-4446-2600（代表）　FAX:03-5371-2552"
)

# 問題点と対策・志望校の行罫線（上端, 下端）。原本実測。
_ISSUE_ROWS = [(873.15, 850.1), (850.1, 827.6), (827.6, 805.1), (805.1, 782.5), (782.5, 759.12)]
_TARGET_ROWS = [(738.18, 715.1), (715.1, 692.6), (692.6, 669.15)]

# 指導実施日（左）・指導予定日（右）カレンダー。列は丸括弧ペアの実測位置。
_CAL_LEFT_PARENS = [(912.2, 932.8), (938.1, 958.9), (967.7, 988.4), (998.1, 1018.9), (1028.4, 1049.1), (1058.3, 1079.0), (1088.4, 1109.1)]
_CAL_RIGHT_PARENS = [(1198.9, 1219.4), (1226.9, 1247.6), (1256.4, 1277.2), (1286.9, 1307.6), (1317.1, 1337.9), (1347.0, 1367.8), (1377.1, 1397.9)]
_CAL_LEFT_COLS = [923.5, 950.0, 980.0, 1010.2, 1040.7, 1070.8, 1100.9]
_CAL_RIGHT_COLS = [1208.9, 1237.6, 1267.6, 1297.7, 1327.9, 1358.0, 1388.15]
_CAL_LEFT_ROW_BASE = [525.7, 511.7, 497.7, 483.7, 469.7]
_CAL_RIGHT_ROW_BASE = [526.9, 512.9, 498.9, 484.9, 470.9]

# テスト結果表の列（左=模試/実力テスト6教科、右=学校5教科）のセル中心x
_MOCK_CELL_X = [948.15, 979.4, 1010.65, 1041.9, 1073.15, 1104.45]
_SCHOOL_CELL_X = [1217.05, 1248.3, 1279.55, 1310.8, 1342.05]


def _next_month(target_month: str) -> tuple[int, int]:
    year, month = (int(v) for v in target_month.split("-"))
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _weekday_char(year: int, month: int, day: int) -> str:
    return _WD[(date(year, month, day).weekday() + 1) % 7]


def _draw_dashed(c, x0, y0, x1, y1, width=0.4):
    c.saveState()
    c.setLineWidth(width)
    c.setDash(1.4, 1.7)
    c.line(x0, y0, x1, y1)
    c.restoreState()


def _circle_mark(c, cx, cy, rx, ry):
    """記入者の丸印（選択肢・実施日の○）。"""
    c.saveState()
    c.setLineWidth(1.1)
    c.ellipse(cx - rx, cy - ry, cx + rx, cy + ry, stroke=1, fill=0)
    c.restoreState()


def _draw_title(c, font, target_month: str):
    """タイトル行（指導月報・年月ボックス・報告用（小学生）バッジ・注意書き）。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    year, month = target_month.split("-")
    c.setFillColorRGB(*_INK)
    c.setStrokeColorRGB(*_INK)
    c.setFont(font, 21.5)
    c.drawString(884.4, _baseline(950.7, 22), "指導月報")
    # 年月ボックス「20（yy）年（m）月分」
    c.setLineWidth(1.0)
    c.rect(982.23, 950.64, 114.67, 21.51, stroke=1, fill=0)
    c.setFont(font, 12.7)
    base = _baseline(955.0, 12.7)
    c.drawString(985.3, base, "20")
    c.drawCentredString(1016.5, base, year[2:])
    c.drawString(1028.7, base, "年")
    c.drawCentredString(1054.7, base, str(int(month)))
    c.drawString(1067.4, base, "月分")
    # 報告用（小学生）バッジ（黒地・白抜き2行）
    c.roundRect(1099.95, 949.93, 69.97, 23.32, 11.6, stroke=1, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 11.5)
    c.drawString(1115.24, _baseline(960.9, 11.5), "報告用")
    c.setFont(font, 10.35)
    c.drawString(1109.06, _baseline(950.9, 10.35), "（小学生）")
    # 注意書き（4行）。「㊞」はIPAフォント未収録のため「印」＋丸囲みで再現する。
    c.setFillColorRGB(*_INK)
    c.setFont(font, 6.2)
    for note, y0 in zip(_HEAD_NOTES, (968.4, 962.0, 955.5, 948.9)):
        base = _baseline(y0, 6.2)
        if "㊞" in note:
            prefix, suffix = note.split("㊞", 1)
            c.drawString(1173.76, base, prefix)
            x_mark = 1173.76 + stringWidth(prefix, font, 6.2)
            c.drawString(x_mark, base, "印")
            c.setLineWidth(0.5)
            c.ellipse(x_mark - 0.5, base - 1.2, x_mark + 6.7, base + 6.0, stroke=1, fill=0)
            c.drawString(x_mark + 6.7, base, suffix)
        else:
            c.drawString(1173.76, base, note)


def _draw_info_table(c, font, member_name, member_no, student_name, grade, tutor_name, tutor_no):
    """会員名・生徒名／会員No.・学年／講師名・講師No.のヘッダー表。学年は月報のフリー入力値。"""
    c.setFillColorRGB(*_INK)
    c.setStrokeColorRGB(*_INK)
    c.setLineWidth(0.99)
    c.rect(882.68, 893.32, 172.91, 51.02, stroke=1, fill=0)
    c.rect(1055.62, 893.31, 172.91, 51.02, stroke=1, fill=0)
    c.rect(1236.85, 893.33, 183.12, 51.02, stroke=1, fill=0)
    c.setLineWidth(0.4)
    for x0, x1 in ((882.71, 1055.52), (1055.67, 1228.48), (1237.17, 1419.60)):
        c.line(x0, 919.02, x1, 919.02)
    for x in (925.43, 1098.32, 1279.60):
        _draw_dashed(c, x, 893.35, x, 944.30, 0.35)

    c.setFont(font, 9.2)
    top_base = _baseline(926.8, 9.2)
    bottom_base = _baseline(901.7, 9.2)
    c.drawString(889.6, top_base, "会員名")
    c.drawString(890.1, bottom_base, "生徒名")
    c.drawString(1058.7, top_base, "会員No.")
    c.drawString(1067.4, bottom_base, "学年")
    c.drawString(1244.2, top_base, "講師名")
    c.drawString(1241.2, bottom_base, "講師No.")

    _draw_fitted(c, font, member_name, 930.0, top_base, 122.0, 9.5)
    _draw_fitted(c, font, student_name, 930.0, bottom_base, 122.0, 9.5)
    _draw_fitted(c, font, member_no, 1103.0, top_base, 122.0, 9.5)
    _draw_fitted(c, font, grade, 1103.0, bottom_base, 122.0, 9.5)
    _draw_fitted(c, font, tutor_name, 1284.0, top_base, 132.0, 9.5)
    _draw_fitted(c, font, tutor_no, 1284.0, bottom_base, 132.0, 9.5)


def _draw_issues(c, font, issues: list[str]):
    """次月に向けての問題点と対策（5行・番号は原本どおり1〜3のみ印字）。"""
    c.setFillColorRGB(*_BAR_GRAY)
    c.setLineWidth(0.99)
    c.rect(882.68, 873.15, 537.47, 15.01, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 10)
    c.drawString(886.4, _baseline(875.7, 10), "次月に向けての問題点と対策")
    c.rect(882.68, 759.12, 537.42, 129.05, stroke=1, fill=0)
    c.setLineWidth(0.4)
    for _, y in _ISSUE_ROWS[:-1]:
        c.line(882.9, y, 1420.2, y)
    _draw_dashed(c, 905.0, 759.12, 905.0, 873.15)
    c.setFont(font, 10)
    for index, (top, _) in enumerate(_ISSUE_ROWS[:3]):
        c.drawCentredString(892.3, top - 16.85, str(index + 1))
    for value, (top, bottom) in zip(issues, _ISSUE_ROWS):
        _draw_block(c, font, value, 910.0, top - 1.0, bottom + 1.0, 502.0, sizes=(9.5, 8.0, 7.0, 6.0))


def _draw_targets(c, font, targets: list[str]):
    """現時点での志望校（左1〜3・右4〜5）。"""
    c.setFillColorRGB(*_BAR_GRAY)
    c.setLineWidth(0.99)
    c.rect(882.68, 738.18, 537.47, 15.01, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 10)
    c.drawString(886.43, _baseline(740.17, 10), "現時点での志望校")
    c.rect(882.68, 669.15, 537.42, 84.05, stroke=1, fill=0)
    c.setLineWidth(0.4)
    for _, y in _TARGET_ROWS[:-1]:
        c.line(882.9, y, 1420.2, y)
    c.line(1141.5, 669.15, 1141.5, 738.18)
    _draw_dashed(c, 905.0, 669.15, 905.0, 738.18)
    _draw_dashed(c, 1161.3, 669.15, 1161.3, 738.18)
    c.setFont(font, 10)
    for index, (top, _) in enumerate(_TARGET_ROWS):
        c.drawCentredString(892.3, top - 16.85, str(index + 1))
    for index, (top, _) in enumerate(_TARGET_ROWS[:2]):
        c.drawCentredString(1151.75, top - 16.85, str(index + 4))
    for value, (top, bottom) in zip(targets[:3], _TARGET_ROWS):
        _draw_block(c, font, value, 910.0, top - 1.0, bottom + 1.0, 224.0, sizes=(9.5, 8.0, 7.0, 6.0))
    for value, (top, bottom) in zip(targets[3:5], _TARGET_ROWS[:2]):
        _draw_block(c, font, value, 1166.0, top - 1.0, bottom + 1.0, 246.0, sizes=(9.5, 8.0, 7.0, 6.0))


def _draw_score_table(c, font, x_label, subjects, cell_xs, scores):
    """素点・偏差値の2段（教科ヘッダーは呼び出し元で描画済み）に値を流し込む。"""
    score_base = _baseline(599.15, 9.5)
    deviation_base = _baseline(580.15, 9.5)
    c.setFont(font, 9.5)
    c.drawString(x_label, score_base, "素点")
    c.drawString(x_label - 4.75, deviation_base, "偏差値")
    for row, base in (("score", score_base), ("deviation", deviation_base)):
        for i in range(len(subjects)):
            value = (scores[i] if i < len(scores) else {}).get(row, "")
            _draw_fitted(c, font, str(value or ""), cell_xs[i], base, 28.0, 9.5, align="center")


def _draw_tests(c, font, mock: dict, school: dict):
    """最近のテスト結果（模試/実力テスト・学校）。"""
    c.setFillColorRGB(*_BAR_GRAY)
    c.setLineWidth(0.99)
    c.rect(882.68, 649.07, 537.32, 14.17, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 10)
    c.drawString(886.2, _baseline(651.6, 10), "最近のテスト結果")
    c.rect(882.68, 574.84, 537.42, 88.40, stroke=1, fill=0)

    # 表罫線（模試の種類行には縦罫なし）
    c.setLineWidth(0.71)
    c.line(901.6, 632.8, 1420.1, 632.8)
    c.line(901.0, 614.3, 1420.1, 614.3)
    c.setLineWidth(0.57)
    c.line(901.8, 595.3, 1419.9, 595.3)
    for x in (932.5, 963.8, 995.0, 1026.3, 1057.5, 1088.8, 1120.1, 1201.4, 1232.7, 1263.9, 1295.2, 1326.4, 1357.6, 1388.9):
        c.line(x, 574.6, x, 632.7)

    # 縦書きラベル（黒地・白抜き）: 模試／実力テスト・学校
    c.rect(882.68, 575.0, 19.32, 74.65, stroke=0, fill=1)
    c.rect(1151.44, 574.84, 19.33, 73.94, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 9.5)
    for ch, y0 in (("模", 639.04), ("試", 629.82), ("／", 621.0), ("実", 613.85), ("力", 604.62), ("テ", 595.69), ("ス", 586.95), ("ト", 578.02)):
        c.drawCentredString(892.5, _baseline(y0, 9.5), ch)
    c.drawCentredString(1161.1, _baseline(612.88, 9.5), "学")
    c.drawCentredString(1161.1, _baseline(603.68, 9.5), "校")
    c.setFillColorRGB(*_INK)

    # ヘッダー行（模試の種類・受験日／（　）期 中間・期末）
    header_base = _baseline(634.77, 10)
    c.setFont(font, 10)
    c.drawString(905.16, header_base, "模試の種類：")
    c.drawString(1049.31, header_base, "受験日：")
    c.drawString(1106.81, header_base, "月")
    c.drawString(1136.81, header_base, "日")
    c.drawString(1171.20, header_base, "（")
    c.drawString(1241.20, header_base, "）期")
    c.drawString(1281.20, header_base, "中間・期末")

    subject_base = _baseline(618.15, 9.5)
    c.setFont(font, 9.5)
    c.drawString(907.63, subject_base, "教科")
    for name, x in zip(MOCK_SUBJECTS, (938.13, 969.36, 1000.59, 1031.92, 1063.16, 1094.40)):
        c.drawString(x, subject_base, name)
    c.drawString(1176.45, subject_base, "教科")
    for name, x in zip(SCHOOL_SUBJECTS, (1207.06, 1238.29, 1269.53, 1300.77, 1332.01)):
        c.drawString(x, subject_base, name)

    # 記入値
    _draw_fitted(c, font, mock.get("name") or "", 967.0, header_base, 80.0, 10)
    if mock.get("exam_month"):
        c.setFont(font, 10)
        c.drawCentredString(1101.8, header_base, str(mock["exam_month"]))
    if mock.get("exam_day"):
        c.setFont(font, 10)
        c.drawCentredString(1131.8, header_base, str(mock["exam_day"]))
    _draw_fitted(c, font, school.get("term") or "", 1211.2, header_base, 56.0, 10, align="center")
    if school.get("term_type") in ("中間", "期末"):
        cx = 1291.2 if school["term_type"] == "中間" else 1321.2
        _circle_mark(c, cx, header_base + 3.4, 12.0, 6.8)
    _draw_score_table(c, font, 907.63, MOCK_SUBJECTS, _MOCK_CELL_X, mock.get("scores") or [])
    _draw_score_table(c, font, 1176.45, SCHOOL_SUBJECTS, _SCHOOL_CELL_X, school.get("scores") or [])


def _draw_calendar_grid(c, font, parens, cols, row_bases, year, month, marked_days):
    """曜日括弧＋日付グリッド（1〜31固定）を描画し、対象日に○印を付ける。"""
    paren_base = _baseline(538.4, 8.2)
    c.setFont(font, 8.2)
    for index, (open_x, close_x) in enumerate(parens):
        c.drawCentredString(open_x, paren_base, "(")
        c.drawCentredString(close_x, paren_base, ")")
        c.setFont(font, 7.6)
        c.drawCentredString((open_x + close_x) / 2, paren_base + 0.4, _weekday_char(year, month, index + 1))
        c.setFont(font, 8.2)
    marked = set(marked_days or [])
    c.setFont(font, 7.3)
    for row, base in enumerate(row_bases):
        for col, x in enumerate(cols):
            day = row * 7 + col + 1
            if day > 31:
                break
            c.drawCentredString(x, base, str(day))
            if day in marked:
                _circle_mark(c, x, base + 2.6, 9.5, 6.2)


def _draw_calendars(c, font, target_month: str, lesson_days, plan_days, total_hours: str):
    """指導実施日（今月）・指導予定日（次月）・指導時間合計。"""
    year, month = (int(v) for v in target_month.split("-"))
    next_year, next_month = _next_month(target_month)

    c.setFillColorRGB(*_BAR_GRAY)
    c.setLineWidth(0.99)
    c.rect(882.81, 554.55, 269.29, 14.17, stroke=1, fill=1)
    c.rect(1152.10, 554.55, 269.29, 14.17, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 10)
    bar_base = _baseline(557.0, 10)
    c.drawString(886.4, bar_base, "指導実施日")
    c.drawString(1156.4, bar_base, "指導予定日")
    c.rect(882.68, 458.17, 538.58, 110.55, stroke=1, fill=0)

    # 区切り（点線=ラベル列・時間合計列、実線=左右カレンダーの境）
    _draw_dashed(c, 903.1, 458.2, 903.1, 554.55, 0.57)
    _draw_dashed(c, 1119.4, 458.2, 1119.4, 554.55, 0.57)
    _draw_dashed(c, 1172.5, 458.2, 1172.5, 554.55, 0.57)
    c.setLineWidth(0.4)
    c.line(1152.3, 458.1, 1152.3, 568.5)

    # 今月・次月の黒ラベルと「月」（月数は自動記入）
    for x0, chars, month_no, label_cx in ((882.81, "今月", month, 892.95), (1152.26, "次月", next_month, 1162.4)):
        c.rect(x0, 523.12, 20.27, 31.08, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(font, 9.5)
        c.drawCentredString(label_cx, _baseline(539.5, 9.5), chars[0])
        c.drawCentredString(label_cx, _baseline(528.7, 9.5), chars[1])
        c.setFillColorRGB(*_INK)
        c.setFont(font, 9.5)
        c.drawCentredString(label_cx, _baseline(479.6, 9.5), "月")
        c.setFont(font, 10)
        c.drawCentredString(label_cx, 505.0, str(month_no))

    _draw_calendar_grid(c, font, _CAL_LEFT_PARENS, _CAL_LEFT_COLS, _CAL_LEFT_ROW_BASE, year, month, lesson_days)
    _draw_calendar_grid(c, font, _CAL_RIGHT_PARENS, _CAL_RIGHT_COLS, _CAL_RIGHT_ROW_BASE, next_year, next_month, plan_days)

    # 指導時間合計（縦書き）＋記入値＋単位
    c.setFont(font, 9.2)
    for index, ch in enumerate("指導時間合計"):
        c.drawCentredString(1135.2, 545.0 - 9.56 * index - 3.4, ch)
    c.drawCentredString(1135.75, _baseline(462.5, 9.2), "時間")
    _draw_fitted(c, font, total_hours or "", 1135.75, 478.5, 30.0, 9.0, align="center")

    c.setFont(font, 6.6)
    note_base = _baseline(460.6, 6.6)
    c.drawString(985.5, note_base, "※実際に指導した日に○印を付けてください。")
    c.drawString(1306.0, note_base, "※指導予定日に○印を付けてください。")


def _draw_choice_row(c, font, base, answer, count, informed):
    """遅刻・指導日の変更の共通行（A なし／B あり（回数）＋事前連絡 a/b）。"""
    c.setFont(font, 9.3)
    c.drawString(966.5, base, "A")
    c.drawString(979.2, base, "なし")
    c.drawString(1018.6, base, "B")
    c.drawString(1030.3, base, "あり")
    c.drawCentredString(1061.4, base, "（")
    c.drawCentredString(1093.5, base, "）")
    c.drawString(1117.0, base, "※Bありの場合、会員に事前連絡を・・・")
    c.drawString(1307.8, base, "a")
    c.drawString(1318.7, base, "した")
    c.drawString(1355.3, base, "b")
    c.drawString(1365.8, base, "しなかった")
    if answer == "A":
        _circle_mark(c, 970.1, base + 3.3, 7.0, 5.9)
    elif answer == "B":
        _circle_mark(c, 1021.7, base + 3.3, 7.0, 5.9)
        if count:
            _draw_fitted(c, font, str(count), 1077.45, base, 26.0, 9.3, align="center")
    if answer == "B" and informed == "a":
        _circle_mark(c, 1310.4, base + 3.3, 6.4, 5.9)
    elif answer == "B" and informed == "b":
        _circle_mark(c, 1357.9, base + 3.3, 6.4, 5.9)


def _draw_makeup_plan_slot(c, font, base, anchors, plan):
    """振替予定（　月　日→　月　日）1組分の数値を右詰めで記入する。"""
    c.setFont(font, 9.3)
    for key, x in zip(("from_month", "from_day", "to_month", "to_day"), anchors):
        value = plan.get(key)
        if value is not None:
            c.drawRightString(x, base, str(value))


def _draw_retrospect(c, font, retro: dict, notes: str):
    """今月を振り返って（遅刻・指導日の変更・変更理由・休んだ日の振替・連絡事項）。"""
    late = retro.get("late") or {}
    change = retro.get("schedule_change") or {}
    reason = retro.get("change_reason") or {}
    makeup = retro.get("makeup") or {}
    plans = [p for p in (makeup.get("plans") or []) if isinstance(p, dict)]

    c.setFillColorRGB(*_BAR_GRAY)
    c.setLineWidth(0.99)
    c.rect(882.78, 437.79, 538.58, 14.55, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 10)
    c.drawString(887.0, _baseline(440.4, 10), "今月を振り返って")
    c.rect(882.78, 292.88, 538.58, 159.46, stroke=1, fill=0)
    c.setLineWidth(0.4)
    for y in (417.0, 399.3, 378.36, 345.14):
        c.line(883.0, y, 1420.6, y)
    _draw_dashed(c, 958.9, 292.88, 958.9, 437.79)

    c.setFont(font, 9.3)
    c.drawString(886.8, _baseline(422.4, 9.3), "遅刻")
    c.drawString(886.9, _baseline(403.6, 9.3), "指導日の変更")
    c.drawString(886.7, _baseline(384.1, 9.3), "変更理由")
    c.drawString(886.8, _baseline(365.8, 9.3), "休んだ日の振替")
    c.drawString(886.8, _baseline(332.9, 9.3), "連絡事項")

    _draw_choice_row(c, font, _baseline(422.6, 9.3), late.get("answer"), late.get("count"), late.get("informed"))
    _draw_choice_row(c, font, _baseline(403.8, 9.3), change.get("answer"), change.get("count"), change.get("informed"))

    # 変更理由: A 会員都合 / B 講師都合（理由：…）
    reason_base = _baseline(384.3, 9.3)
    c.setFont(font, 9.3)
    c.drawString(966.5, reason_base, "A")
    c.drawString(978.9, reason_base, "会員都合")
    c.drawString(1040.0, reason_base, "B")
    c.drawString(1051.7, reason_base, "講師都合")
    c.drawString(1098.5, reason_base, "（理由：")
    c.drawCentredString(1408.15, reason_base, "）")
    if reason.get("answer") == "A":
        _circle_mark(c, 970.1, reason_base + 3.3, 7.0, 5.9)
    elif reason.get("answer") == "B":
        _circle_mark(c, 1043.05, reason_base + 3.3, 7.0, 5.9)
    _draw_fitted(c, font, reason.get("reason") or "", 1140.0, reason_base, 262.0, 9.3)

    # 休んだ日の振替: A 当月中に済 / B 振替日未定 / C 振替予定あり（…）
    makeup_base1 = _baseline(365.0, 9.3)
    c.setFont(font, 9.3)
    c.drawString(966.5, makeup_base1, "A")
    c.drawString(979.3, makeup_base1, "当月中に済")
    c.drawString(1039.7, makeup_base1, "B")
    c.drawString(1049.8, makeup_base1, "振替日未定")
    makeup_base2 = _baseline(348.9, 9.3)
    c.drawString(966.4, makeup_base2, "C")
    c.drawString(978.2, makeup_base2, "振替予定あり")
    c.drawCentredString(1042.9, makeup_base2, "（")
    for ch, x in (("月", 1064.1), ("日", 1093.7), ("→", 1102.5), ("月", 1140.5), ("日", 1170.1), ("、", 1179.0), ("月", 1202.5), ("日", 1232.1), ("→", 1240.9), ("月", 1278.9), ("日", 1308.5)):
        c.drawString(x, makeup_base2, ch)
    c.drawCentredString(1321.1, makeup_base2, "）")
    if makeup.get("answer") == "A":
        _circle_mark(c, 970.15, makeup_base1 + 3.3, 7.0, 5.9)
    elif makeup.get("answer") == "B":
        _circle_mark(c, 1042.8, makeup_base1 + 3.3, 7.0, 5.9)
    elif makeup.get("answer") == "C":
        _circle_mark(c, 969.75, makeup_base2 + 3.3, 7.0, 5.9)
    if plans:
        _draw_makeup_plan_slot(c, font, makeup_base2, (1062.1, 1091.7, 1138.5, 1168.1), plans[0])
    if len(plans) > 1:
        _draw_makeup_plan_slot(c, font, makeup_base2, (1200.5, 1230.1, 1276.9, 1306.5), plans[1])
    if len(plans) > 2:
        # 3組目以降は印字枠が無いため「）」の右へ縮小して補記する（原本にない自由追加分）
        extra = "、".join(
            f"{p.get('from_month') or '?'}/{p.get('from_day') or '?'}→{p.get('to_month') or '?'}/{p.get('to_day') or '?'}"
            for p in plans[2:]
        )
        text, size = _fit_line(c, font, f"、{extra}", 90.0, 8.0)
        if text:
            c.setFont(font, size)
            c.drawString(1327.0, makeup_base2, text)

    _draw_block(c, font, notes, 967.0, 331.0, 294.5, 448.0, sizes=(9.5, 8.5, 7.5, 6.5))


def _draw_member_stamp(c, font, approved_at, parent_name: str):
    """会員認め印（保護者承認の電子印・朱色の二重丸）。指導日報PDFと同意匠。"""
    from reportlab.lib import colors

    cx, cy = 1390.96, 201.9
    red = colors.HexColor(_STAMP_RED)
    approved = _to_jst(approved_at)
    c.saveState()
    c.setStrokeColor(red)
    c.setFillColor(red)
    c.setLineWidth(1.1)
    c.circle(cx, cy, 15.2, stroke=1, fill=0)
    c.setLineWidth(0.9)
    c.circle(cx, cy, 12.0, stroke=1, fill=0)
    c.setFont(font, 4.8)
    c.drawCentredString(cx, cy + 6.2, f"{approved.month}/{approved.day}")
    c.setFont(font, 5.6)
    c.drawCentredString(cx, cy - 1.8, "会員")
    c.setFont(font, 4.8)
    c.drawCentredString(cx, cy - 8.8, (parent_name or "")[:4])
    c.restoreState()


def _draw_parent_area(c, font, parent_note: str, stamp_source):
    """保護者記入欄（ご要望/連絡事項・会員認め印）。stamp_source=押印の根拠となる承認済み報告書。"""
    c.setFillColorRGB(*_FRAME_GRAY)
    c.rect(882.26, 179.75, 538.58, 103.72, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.rect(905.33, 182.65, 512.61, 83.12, stroke=0, fill=1)
    c.setFillColorRGB(*_BAR_GRAY)
    c.setStrokeColorRGB(*_INK)
    c.setLineWidth(0.99)
    c.rect(882.59, 265.77, 537.92, 17.69, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 10)
    c.drawString(909.2, _baseline(269.5, 10), "ご要望 / 連絡事項")
    # 縦書きラベル（黒地・白抜き）
    c.rect(882.59, 179.75, 22.74, 103.72, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 9.8)
    for ch, cy in zip("保護者記入欄", (259.5, 248.4, 237.2, 225.9, 214.9, 203.7)):
        c.drawCentredString(894.0, cy - 3.6, ch)
    # 会員認め印欄（プレースホルダ→承認済みなら電子印を重ねる）
    c.setFillColorRGB(1, 1, 1)
    c.setLineWidth(0.93)
    c.rect(1367.07, 185.42, 47.77, 32.96, stroke=1, fill=1)
    c.setFillColorRGB(*_PLACEHOLDER_GRAY)
    c.setFont(font, 10)
    c.drawCentredString(1391.95, 204.0, "会員")
    c.drawCentredString(1391.95, 191.5, "認め印")
    c.setFillColorRGB(*_INK)
    # 保護者記入内容（認め印欄と重ならない幅で折返し）
    _draw_block(c, font, parent_note, 910.0, 261.0, 186.5, 445.0, sizes=(9.5, 8.5, 7.5, 6.5))
    if stamp_source is not None:
        parent = stamp_source.parent
        _draw_member_stamp(c, font, stamp_source.parent_approved_at, parent.display_name if parent else "")


def _stamp_source(reports: list):
    """会員認め印の根拠（保護者承認を通過し現在も有効な報告書のうち最新の承認）を返す。"""
    stamped = [r for r in reports if _has_member_stamp(r)]
    if not stamped:
        return None
    return max(stamped, key=lambda r: r.parent_approved_at)


def _draw_page(c, font, monthly, reports: list):
    """1ページ（指導月報1件）を描画する。"""
    c.saveState()
    c.scale(595.28 / _SRC_W, 841.89 / _SRC_H)
    c.translate(-_SRC_X0, -_SRC_Y0)

    assignment = monthly.assignment
    tutor = monthly.tutor
    parent = assignment.parent if assignment else None
    data = normalize_form_data(monthly.form_data)

    _draw_title(c, font, monthly.target_month)
    _draw_info_table(
        c, font,
        member_name=parent.display_name if parent else "",
        member_no=(parent.user_no if parent and parent.user_no else ""),
        student_name=assignment.student_name if assignment else "",
        grade=monthly.grade or "",
        tutor_name=tutor.display_name if tutor else "",
        tutor_no=(tutor.user_no if tutor and tutor.user_no else ""),
    )
    _draw_issues(c, font, data["issues"])
    _draw_targets(c, font, data["target_schools"])
    _draw_tests(c, font, data["test_mock"], data["test_school"])
    _draw_calendars(c, font, monthly.target_month, data["lesson_days"], data["next_month_plan_days"], data["total_hours"])
    _draw_retrospect(c, font, data["retrospect"], data["notes"])
    _draw_parent_area(c, font, monthly.parent_note or "", _stamp_source(reports))

    c.setFillColorRGB(*_INK)
    text, size = _fit_line(c, font, _COMPANY_FOOTER, 500.0, 8.0)
    c.setFont(font, size)
    c.drawCentredString(1151.55, _baseline(163.98, 8), text)
    c.restoreState()
    c.showPage()


def build_monthly_reports_pdf(entries: list, target_month: str) -> bytes:
    """指導月報PDFを生成する。entries は (MonthlyReport, 対象月の報告書list) のリスト。

    担当（assignment）ごとに1ページ。会員認め印は対象月の報告書が保護者承認を
    通過している（現在も有効）場合のみ描画する（指導日報PDFと同一判定）。
    """
    font = register_pdf_font()
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    def _entry_key(entry):
        monthly, _ = entry
        assignment = monthly.assignment
        student = assignment.student_name if assignment else ""
        tutor = monthly.tutor.display_name if monthly.tutor else ""
        return (student, tutor)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setTitle("指導月報")
    for monthly, reports in sorted(entries, key=_entry_key):
        _draw_page(c, font, monthly, reports)
    c.save()
    return buf.getvalue()
# === 指導月報PDF END ===
