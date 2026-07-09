# === 指導日報PDF START ===
"""「指導日報」PDFの生成（原本: docs/イスト勤怠レポート for 代々木進学会/原本_日報.pdf）。

紙の指導日報（1枚に5日分・日ごとに会員認め印欄）を忠実に再現し、講師の指導報告
（LessonReport 1件＝1指導日）を1枠ずつ流し込む。保護者（会員）承認を通過した報告書
には会員認め印欄へ電子印（朱色）を描画する。

座標系は原本PDF（版下・トンボ付き B4系用紙）の座標をそのまま使い、仕上がり枠
（トンボ内側）をA4縦へ線形変換して描画する。原本から実測した座標を変更する場合は
docs の原本PDFと突き合わせること。
"""
import io
from collections import defaultdict
from datetime import datetime, timezone

from app.core.time import JST
from app.services.pdf_fonts import register_pdf_font

# 原本の仕上がり枠（トンボ内側）。この範囲をA4(595.28x841.89pt)全面へ写像する。
_SRC_X0, _SRC_Y0 = 48.61, 76.96
_SRC_W, _SRC_H = 631.28, 877.89

# 原本の色（リッチブラック／罫線・文字）、枠内グレー、認め印プレースホルダの淡灰
_INK = (0.135, 0.094, 0.082)
_GRAY_FILL = (0.896, 0.898, 0.899)
_PLACEHOLDER_GRAY = (0.901, 0.902, 0.903)
_STAMP_RED = "#c81e1e"

_WD = ["日", "月", "火", "水", "木", "金", "土"]

# 1ページの枠数と枠の縦ピッチ（原本実測）
FRAMES_PER_PAGE = 5
_FRAME_PITCH = 144.80

# 会員認め印を描画する状態（保護者承認を通過し、その承認が現在も有効な状態）。
# returned_to_tutor（差戻し中）と closed（無効クローズ）では承認済みでも押印しない。
_STAMPED_STATUSES = {
    "parent_approved",
    "submitted_to_admin",
    "received",
    "re_reviewed",
    "admin_approved",
    "returned_to_receiver",
}

_COMPANY_FOOTER = (
    "株式会社イスト 〒151-0053 東京都渋谷区代々木1-35-4代々木クリスタルビル 5F "
    "tel.03-4446-2600(代)   fax.03-5371-2552"
)
_HEAD_NOTES = [
    "■空欄のないように太枠内を全て記入すること。(鉛筆不可) ■授業終了後に記入すること (授業中に",
    "作成しないこと)  ■会員認め㊞が1ヶ所でも漏れている場合は、会で受付が出来ませんのでご注意く",
    "ださい。 ■1枚目が講師報告用、 2枚目がご家庭控えとなります。",
]

# 左端の縦書きラベル「理解度／問題点（具体的に詳しく）本日の指導内容」
# （列のx中心, [(文字, 原本の基準y), ...]）。丸括弧は縦書きのため回転描画する。
_VERT_LABEL_COLS = [
    (103.98, [("理", 803.85), ("解", 795.26), ("度", 786.68), ("／", 778.09), ("問", 769.51), ("題", 760.92), ("点", 752.34)]),
    (123.29, [("本", 803.85), ("日", 795.43), ("の", 787.19), ("指", 778.95), ("導", 770.37), ("内", 761.78), ("容", 753.20)]),
]
_VERT_PAREN_COL = (113.63, [("（", 810.6), ("具", 801.41), ("体", 792.82), ("的", 784.24), ("に", 776.00), ("詳", 767.84), ("し", 759.34), ("く", 750.93), ("）", 744.6)])

# ⓓ宿題：状況 A/B/C の文字位置（円で囲むため個別描画する）
_HOMEWORK_MARKS = {"A": 283.05, "B": 360.10, "C": 446.75}
# 学年 小・中・高 の文字位置（円で囲むため個別描画する）
_GRADE_MARKS = {"小": 327.65, "中": 346.35, "高": 364.15}


def _to_jst(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST)


def _baseline(y0: float, size: float) -> float:
    # 原本から実測したのはグリフ枠下端(y0)。ベースラインへ補正して描画する。
    return y0 + 0.12 * size


def _wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    """日本語前提の文字単位折返し（入力の改行は維持する）。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        buf = ""
        for ch in raw:
            if buf and stringWidth(buf + ch, font, size) > width:
                lines.append(buf)
                buf = ch
            else:
                buf += ch
        lines.append(buf)
    return lines


def _draw_block(c, font, text, x, top, bottom, width, sizes=(8.0, 7.0, 6.0, 5.2)):
    """枠内に収まる最大サイズで折返し描画（縦中央寄せ）。収まらない場合は末尾を…で省略。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    text = (text or "").strip()
    if not text:
        return
    height = top - bottom
    size = sizes[-1]
    lines = None
    for candidate in sizes:
        leading = candidate * 1.18
        max_lines = max(1, int((height - 1.2) // leading))
        wrapped = _wrap_text(text, font, candidate, width)
        if len(wrapped) <= max_lines:
            size, lines = candidate, wrapped
            break
    if lines is None:
        leading = size * 1.18
        max_lines = max(1, int((height - 1.2) // leading))
        lines = _wrap_text(text, font, size, width)[:max_lines]
        last = lines[-1]
        while last and stringWidth(last + "…", font, size) > width:
            last = last[:-1]
        lines[-1] = last + "…"
    leading = size * 1.18
    block_h = leading * (len(lines) - 1) + size
    y = bottom + (height - block_h) / 2 + block_h - size * 0.88
    c.setFont(font, size)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading


def _fit_line(c, font, text, width, size, min_size=5.0):
    """1行で収まるサイズに縮小して (描画テキスト, サイズ) を返す。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    text = (text or "").strip()
    if not text:
        return "", size
    while size > min_size and stringWidth(text, font, size) > width:
        size -= 0.5
    while text and stringWidth(text, font, size) > width:
        text = text[:-1]
    return text, size


def _draw_fitted(c, font, text, x, y, width, size, align="left"):
    text, fitted = _fit_line(c, font, text, width, size)
    if not text:
        return
    c.setFont(font, fitted)
    if align == "center":
        c.drawCentredString(x, y, text)
    elif align == "right":
        c.drawRightString(x, y, text)
    else:
        c.drawString(x, y, text)
    c.setFont(font, size)


def _draw_vertical_paren(c, font, ch, x_center, y_center):
    """縦書き用に丸括弧を90度回転して描画する。"""
    c.saveState()
    c.translate(x_center, y_center)
    c.rotate(-90)
    c.setFont(font, 8.6)
    c.drawCentredString(0, -3.0, ch)
    c.restoreState()


def _draw_title(c, font, target_month: str):
    """タイトル行（指導日報・年月ボックス・報告用バッジ・注意書き）。"""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    year, month = target_month.split("-")
    c.setFillColorRGB(*_INK)
    c.setStrokeColorRGB(*_INK)
    c.setFont(font, 21.5)
    c.drawString(93.3, _baseline(894.5, 22), "指導日報")
    # 年月ボックス「20（yy）年（m）月分」
    c.setLineWidth(1.42)
    c.rect(181.98, 899.10, 105.99, 23.90, stroke=1, fill=0)
    c.setFont(font, 12.7)
    base = _baseline(900.8, 12.7)
    c.drawString(185.0, base, "20")
    c.drawCentredString(210.6, base, year[2:])
    c.drawString(221.9, base, "年")
    c.drawCentredString(247.35, base, str(int(month)))
    c.drawString(260.1, base, "月分")
    # 報告用バッジ（黒地・白抜き）
    c.roundRect(291.87, 899.10, 63.08, 23.90, 8.5, stroke=1, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 15.2)
    c.drawString(300.8, _baseline(902.6, 15.2), "報告用")
    # 注意書き（3行）。「㊞」はIPAフォント未収録のため「印」＋丸囲みで再現する。
    c.setFillColorRGB(*_INK)
    c.setFont(font, 6.2)
    for index, note in enumerate(_HEAD_NOTES):
        base = _baseline(913.8 - 7.2 * index, 6.2)
        if "㊞" in note:
            prefix, suffix = note.split("㊞", 1)
            c.drawString(358.7, base, prefix)
            x_mark = 358.7 + stringWidth(prefix, font, 6.2)
            c.drawString(x_mark, base, "印")
            c.setLineWidth(0.5)
            c.ellipse(x_mark - 0.5, base - 1.2, x_mark + 6.7, base + 6.0, stroke=1, fill=0)
            c.drawString(x_mark + 6.7, base, suffix)
        else:
            c.drawString(358.7, base, note)


def _draw_info_table(c, font, member_name, member_no, student_name, grade_level, grade_year, tutor_name, tutor_no):
    """会員名・生徒名・会員No.・学年・講師名・講師No.のヘッダー表。"""
    c.setFillColorRGB(*_INK)
    c.setStrokeColorRGB(*_INK)
    # 外枠（左ブロック・右ブロック）と内側罫線
    c.setLineWidth(1.38)
    c.rect(94.96, 845.55, 337.78, 48.19, stroke=1, fill=0)
    c.rect(438.04, 845.55, 195.51, 48.19, stroke=1, fill=0)
    c.line(273.26, 845.55, 273.26, 893.74)
    c.line(438.04, 869.65, 633.54, 869.65)
    c.setLineWidth(0.69)
    c.line(94.96, 869.65, 432.74, 869.65)
    c.setLineWidth(0.28)
    for x in (137.48, 315.78, 485.57):
        c.line(x, 845.55, x, 893.74)

    c.setFont(font, 8.5)
    top_base = _baseline(875.05, 8.5)
    bottom_base = _baseline(851.43, 8.5)
    c.drawString(103.06, top_base, "会員名")
    c.drawString(103.06, bottom_base, "生徒名")
    c.drawString(278.56, top_base, "会員No.")
    c.drawString(285.12, bottom_base, "学年")
    c.drawString(446.66, top_base, "講師名")
    c.drawString(446.66, bottom_base, "講師No.")
    # 学年テンプレート「小・中・高 (　　)年」は円囲みのため文字ごとに原本位置へ描画
    grade_base = _baseline(852.1, 8.5)
    for ch, x in (("・", 332.7), ("・", 351.4), ("(", 371.8), (")", 404.5), ("年", 409.7)):
        c.drawString(x, grade_base, ch)
    for ch, x in _GRADE_MARKS.items():
        c.drawString(x - 4.25, grade_base, ch)

    # 記入値
    _draw_fitted(c, font, member_name, 143.0, top_base, 126.0, 9.0)
    _draw_fitted(c, font, student_name, 143.0, bottom_base, 126.0, 9.0)
    _draw_fitted(c, font, member_no, 321.5, top_base, 107.0, 9.0)
    _draw_fitted(c, font, tutor_name, 491.5, top_base, 138.0, 9.0)
    _draw_fitted(c, font, tutor_no, 491.5, bottom_base, 138.0, 9.0)
    if grade_level in _GRADE_MARKS:
        cx = _GRADE_MARKS[grade_level]
        c.setLineWidth(1.0)
        c.ellipse(cx - 6.6, 855.7 - 5.4, cx + 6.6, 855.7 + 5.4, stroke=1, fill=0)
    if grade_year is not None:
        c.setFont(font, 8.5)
        c.drawCentredString(389.5, grade_base, str(grade_year))


def _draw_frame_template(c, font, oy: float):
    """1日分の枠テンプレート（罫線・ラベル・認め印欄）。oy=枠1からの縦オフセット。"""
    c.setFillColorRGB(*_INK)
    c.setStrokeColorRGB(*_INK)

    # 指導日ヘッダー行（黒ラベル・在室した時間帯・教科）
    c.rect(94.96, 822.38 + oy, 32.73, 17.01, stroke=0, fill=1)
    c.setFillColorRGB(*_GRAY_FILL)
    c.setLineWidth(0.28)
    c.rect(241.86, 822.38 + oy, 111.80, 17.01, stroke=1, fill=1)
    c.rect(527.78, 822.38 + oy, 29.60, 17.01, stroke=0, fill=1)
    c.setFillColorRGB(*_INK)
    c.setLineWidth(0.99)
    c.line(94.96, 822.38 + oy, 633.54, 822.38 + oy)
    c.setLineWidth(0.71)
    c.line(241.86, 822.38 + oy, 241.86, 839.39 + oy)
    c.line(527.78, 822.38 + oy, 527.78, 839.39 + oy)
    c.setLineWidth(0.28)
    c.line(557.38, 822.38 + oy, 557.38, 839.39 + oy)

    # 本日の指導内容エリアの行罫線（ⓐ/ⓑ/ⓒ/ⓓ）
    c.setLineWidth(0.57)
    for y in (799.71, 777.03, 754.35):
        c.line(132.34, y + oy, 633.54, y + oy)
    c.line(94.96, 736.92 + oy, 633.54, 736.92 + oy)

    # 左端の縦書きラベル列（グレー地）
    c.setFillColorRGB(*_GRAY_FILL)
    c.setLineWidth(0.71)
    c.rect(94.96, 736.84 + oy, 37.38, 85.61, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setFont(font, 8.6)
    for x_center, chars in _VERT_LABEL_COLS:
        for ch, y in chars:
            c.drawCentredString(x_center, _baseline(y, 8.6) + oy, ch)
    x_center, chars = _VERT_PAREN_COL
    for ch, y in chars:
        if ch in ("（", "）"):
            _draw_vertical_paren(c, font, ch, x_center, y + oy)
        else:
            c.drawCentredString(x_center, _baseline(y, 8.6) + oy, ch)

    # ⓐⓑⓒ の項目ラベル（角丸枠）
    c.setLineWidth(0.28)
    for y_bottom in (808.45, 785.69, 763.01):
        c.roundRect(132.34, y_bottom + oy, 121.98, 13.93, 0.85, stroke=1, fill=0)
    c.setFont(font, 8)
    c.drawString(137.9, _baseline(809.59, 8) + oy, "ⓐ使用教材 ： テキスト名")
    c.drawString(137.79, _baseline(786.86, 8) + oy, "ⓑ何を指導したか ： 単元など")
    c.drawString(137.79, _baseline(764.22, 8) + oy, "ⓒ学習状況 ： 問題点と対策")
    # ⓓ宿題：状況（A/B/C は円囲み対象のため個別描画）
    d_base = _baseline(739.5, 8) + oy
    c.drawString(137.79, d_base, "ⓓ宿題 ： 状況")
    for mark, cx in _HOMEWORK_MARKS.items():
        c.drawString(cx - 2.65, d_base, mark)
    c.drawString(293.7, d_base, "ほぼ完璧")
    c.drawString(370.5, d_base, "半分くらい")
    c.drawString(457.2, d_base, "やらなかった")

    # 次回までの宿題 行
    c.setLineWidth(0.28)
    c.rect(94.96, 722.91 + oy, 68.96, 13.93, stroke=1, fill=0)
    c.drawString(101.42, _baseline(724.12, 8) + oy, "次回までの宿題")
    c.setLineWidth(1.42)
    c.line(94.96, 715.66 + oy, 633.54, 715.66 + oy)

    # 次回の予定 行
    c.setFillColorRGB(*_GRAY_FILL)
    c.setLineWidth(0.20)
    c.rect(95.59, 699.08 + oy, 54.03, 16.58, stroke=1, fill=1)
    c.setLineWidth(0.28)
    c.rect(149.62, 699.08 + oy, 40.21, 16.58, stroke=1, fill=0)
    c.rect(317.33, 699.08 + oy, 65.67, 16.58, stroke=1, fill=1)
    c.setFillColorRGB(*_INK)
    c.setLineWidth(0.71)
    c.line(317.33, 699.08 + oy, 317.33, 715.66 + oy)
    c.line(461.09, 699.08 + oy, 461.09, 715.66 + oy)
    next_base = _baseline(701.1, 8) + oy
    c.setFont(font, 8)
    c.drawString(102.64, next_base, "次回の予定")
    c.drawString(158.3, next_base, "指導日")
    c.drawString(326.3, next_base, "指導開始時刻")
    for ch, x in (("月", 221.1), ("日", 261.1), ("曜", 294.9), ("日", 302.9), ("：", 418.0)):
        c.drawString(x, next_base, ch)

    # 指導日行のテンプレート文字
    header_base = _baseline(825.0, 8) + oy
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font, 9)
    c.drawString(98.83, _baseline(824.53, 9) + oy, "指導日")
    c.setFillColorRGB(*_INK)
    c.setFont(font, 8)
    for ch, x in (("月", 156.1), ("日", 190.0), ("曜", 222.0), ("日", 230.0)):
        c.drawString(x, header_base, ch)
    c.drawString(247.8, header_base, "在室した時間帯(休憩の時間)")
    for ch, x in (("：", 382.4), ("〜", 414.3), ("：", 453.1), ("(", 493.7), ("分", 513.5), (")", 521.5)):
        c.drawString(x, header_base, ch)
    c.drawString(534.88, header_base, "教科")

    # 枠外周（太枠）
    c.setLineWidth(1.42)
    c.rect(94.96, 699.08 + oy, 538.58, 140.31, stroke=1, fill=0)

    # 会員認め印欄（白地で罫線の上に重ねる）
    c.setFillColorRGB(1, 1, 1)
    c.rect(586.37, 703.71 + oy, 36.89, 36.89, stroke=1, fill=1)
    c.setFillColorRGB(*_PLACEHOLDER_GRAY)
    c.setFont(font, 10)
    c.drawString(594.75, _baseline(721.50, 10) + oy, "会員")
    c.drawString(589.54, _baseline(709.49, 10) + oy, "認め印")
    c.setFillColorRGB(*_INK)


def _draw_member_stamp(c, font, report, oy: float):
    """会員認め印（保護者承認の電子印・朱色の二重丸）。"""
    from reportlab.lib import colors

    cx, cy = 604.82, 722.16 + oy
    red = colors.HexColor(_STAMP_RED)
    c.saveState()
    c.setStrokeColor(red)
    c.setFillColor(red)
    c.setLineWidth(1.1)
    c.circle(cx, cy, 16.4, stroke=1, fill=0)
    c.setLineWidth(0.9)
    c.circle(cx, cy, 12.9, stroke=1, fill=0)
    approved_at = _to_jst(report.parent_approved_at)
    parent_name = report.parent.display_name if report.parent else ""
    c.setFont(font, 4.8)
    c.drawCentredString(cx, cy + 6.6, f"{approved_at.month}/{approved_at.day}")
    c.setFont(font, 5.6)
    c.drawCentredString(cx, cy - 1.8, "会員")
    c.setFont(font, 4.8)
    c.drawCentredString(cx, cy - 9.4, parent_name[:4])
    c.restoreState()


def _has_member_stamp(report) -> bool:
    return bool(report.parent_approved_at) and report.status in _STAMPED_STATUSES


def _draw_frame_values(c, font, report, oy: float):
    """1日分の記入値（指導日・在室時間帯・教科・ⓐ〜ⓓ・次回宿題・次回の予定）。"""
    c.setFillColorRGB(*_INK)
    lesson_date = report.lesson_date
    header_base = _baseline(825.0, 8) + oy
    c.setFont(font, 8)
    c.drawRightString(154.1, header_base, str(lesson_date.month))
    c.drawRightString(188.0, header_base, str(lesson_date.day))
    c.drawRightString(220.0, header_base, _WD[(lesson_date.weekday() + 1) % 7])
    c.drawRightString(381.0, header_base, str(report.start_time.hour))
    c.drawString(392.4, header_base, f"{report.start_time.minute:02d}")
    c.drawRightString(451.7, header_base, str(report.end_time.hour))
    c.drawString(463.1, header_base, f"{report.end_time.minute:02d}")
    c.drawCentredString(504.85, header_base, str(report.break_minutes or 0))
    _draw_fitted(c, font, report.subject, 595.46, header_base, 72.0, 8.0, align="center")

    _draw_block(c, font, report.material_name, 258.0, 821.4 + oy, 800.7 + oy, 372.0)
    _draw_block(c, font, report.content, 258.0, 798.7 + oy, 778.0 + oy, 372.0)
    _draw_block(c, font, report.learning_status, 258.0, 776.0 + oy, 755.3 + oy, 372.0)
    if report.homework_status in _HOMEWORK_MARKS:
        cx = _HOMEWORK_MARKS[report.homework_status]
        cy = _baseline(739.5, 8) + oy + 2.9
        c.setLineWidth(1.1)
        c.ellipse(cx - 7.2, cy - 5.6, cx + 7.2, cy + 5.6, stroke=1, fill=0)
    _draw_block(c, font, report.next_homework, 170.0, 736.0 + oy, 716.6 + oy, 458.0)

    next_base = _baseline(701.1, 8) + oy
    c.setFont(font, 8)
    if report.next_lesson_date:
        c.drawRightString(220.1, next_base, str(report.next_lesson_date.month))
        c.drawRightString(260.1, next_base, str(report.next_lesson_date.day))
        c.drawRightString(293.9, next_base, _WD[(report.next_lesson_date.weekday() + 1) % 7])
    if report.next_lesson_start:
        c.drawRightString(417.0, next_base, str(report.next_lesson_start.hour))
        c.drawString(427.0, next_base, f"{report.next_lesson_start.minute:02d}")

    if _has_member_stamp(report):
        _draw_member_stamp(c, font, report, oy)


def _draw_page(c, font, target_month, header_source, grade_source, frame_reports):
    """1ページ（タイトル＋ヘッダー表＋5枠＋フッター）を描画する。"""
    c.saveState()
    c.scale(595.28 / _SRC_W, 841.89 / _SRC_H)
    c.translate(-_SRC_X0, -_SRC_Y0)

    _draw_title(c, font, target_month)
    assignment = header_source.assignment
    tutor = header_source.tutor
    parent = header_source.parent
    _draw_info_table(
        c, font,
        member_name=parent.display_name if parent else "",
        member_no=(parent.user_no if parent and parent.user_no else ""),
        student_name=assignment.student_name if assignment else "",
        grade_level=grade_source.grade_level if grade_source else None,
        grade_year=grade_source.grade_year if grade_source else None,
        tutor_name=tutor.display_name if tutor else "",
        tutor_no=(tutor.user_no if tutor and tutor.user_no else ""),
    )
    for index in range(FRAMES_PER_PAGE):
        oy = -_FRAME_PITCH * index
        _draw_frame_template(c, font, oy)
        if index < len(frame_reports):
            _draw_frame_values(c, font, frame_reports[index], oy)

    c.setFillColorRGB(*_INK)
    c.setFont(font, 8)
    c.drawCentredString(364.25, _baseline(107.71, 8), _COMPANY_FOOTER)
    c.restoreState()
    c.showPage()


def build_daily_reports_pdf(reports: list, target_month: str) -> bytes:
    """指導日報PDFを生成する。担当（assignment）ごとに改ページし、1ページ5日分。

    reports は同一 target_month の LessonReport（assignment/tutor/parent ロード済み）。
    月内の指導日は日付順に流し込み、余った枠は未記入のまま出力する（紙の様式と同じ）。
    """
    font = register_pdf_font()
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas

    grouped: dict = defaultdict(list)
    for report in reports:
        grouped[report.assignment_id].append(report)

    def _group_key(items):
        first = items[0]
        student = first.assignment.student_name if first.assignment else ""
        tutor = first.tutor.display_name if first.tutor else ""
        return (student, tutor)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setTitle("指導日報")
    for items in sorted(grouped.values(), key=_group_key):
        items = sorted(items, key=lambda r: (r.lesson_date, r.start_time.strftime("%H:%M")))
        # ヘッダーの学年は月内で最後に記入された学年（区分＋学年数が揃っている最新の指導日）を使う
        graded = [r for r in items if r.grade_level and r.grade_year]
        grade_source = graded[-1] if graded else None
        for start in range(0, len(items), FRAMES_PER_PAGE):
            _draw_page(c, font, target_month, items[0], grade_source, items[start:start + FRAMES_PER_PAGE])
    c.save()
    return buf.getvalue()
# === 指導日報PDF END ===
