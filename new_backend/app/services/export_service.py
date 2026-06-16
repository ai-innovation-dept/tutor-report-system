"""PDF / CSV エクスポートサービス（monthly_dispatch フォーム対応）。"""
import csv
import io
import os
import re
from datetime import date, datetime

from app.forms.definitions import get_form
from app.models.work import WorkReport
from app.services.report_service import ATTENDANCE_LABELS, is_leave_kind

_PDF_FONT_NAME = "WorkReportFont"
_PDF_FONT_REGISTERED = False


def _font_paths() -> list[str]:
    return [
        os.environ.get("PDF_JP_FONT_PATH", ""),
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/NotoSansJP-VF.ttf",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
    ]


def _register_font() -> str:
    global _PDF_FONT_REGISTERED
    if _PDF_FONT_REGISTERED:
        return _PDF_FONT_NAME
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc
    for path in _font_paths():
        if path and os.path.isfile(path):
            try:
                pdfmetrics.registerFont(TTFont(_PDF_FONT_NAME, path))
                _PDF_FONT_REGISTERED = True
                return _PDF_FONT_NAME
            except Exception:
                continue
    raise RuntimeError("Japanese PDF font not found")


# 長文・複数値で横にはみ出し得る列は Paragraph で折り返す（内容・担当時限）。
_PDF_WRAP_KEYS = ("note", "subject_period")

# 種別列。スナップショット列定義(form_data.meta.column_definition)には含まれないため、
# 講師フォーム・参照ビュー(report_view)・PDF で共通して日付の直後へ差し込む。
_KIND_COLUMN = {"key": "kind", "label": "種別", "type": "kind"}
# date.weekday() は月=0。参照ビューと同じ曜日表記にするための並び。
_PDF_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]


def _weekday(date_str) -> str:
    try:
        return _PDF_WEEKDAYS[date.fromisoformat(str(date_str)).weekday()]
    except (TypeError, ValueError):
        return ""


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _has_data(line: dict) -> bool:
    """記入のある行か（参照ビューの hasData と同条件。種別のみの行も対象に含む）。"""
    return isinstance(line, dict) and any(str(value).strip() for value in line.values())


def _snapshot_columns(report: WorkReport) -> list[dict]:
    """報告書に保存された列定義スナップショット(form_data.meta.column_definition)を返す。

    参照ビュー(report_view)・CSV と同じ列・値・集計で出力するための唯一の情報源。
    動的列（担当業務①〜③・副業務①〜⑤・採点）はここに保存されている。
    スナップショットの無い旧データは静的フォーム定義へフォールバックする。
    """
    meta = (report.form_data or {}).get("meta") or {}
    cols = meta.get("column_definition")
    if isinstance(cols, list) and cols:
        return cols
    form = get_form(report.form_type)
    return [
        {"key": c.key, "label": c.label, "type": c.type, "summable": c.summable}
        for c in form.columns
    ]


def _pdf_cell(column: dict, text, cell_style):
    """セル値を返す。折り返し対象列は Paragraph（XMLエスケープ＋改行→<br/>）にして枠内に収める。"""
    from xml.sax.saxutils import escape
    from reportlab.platypus import Paragraph

    text = str(text if text is not None else "")
    if column.get("key") in _PDF_WRAP_KEYS and text.strip():
        return Paragraph(escape(text).replace("\n", "<br/>"), cell_style)
    return text


def _display_columns(report: WorkReport) -> list[dict]:
    """表示列。日付の直後に種別を差し込み、開始/終了を1列(業務開始〜終了時間)へ結合する。

    参照ビュー(report_view.html)の displayColumns と同一の並び・列にする。
    """
    src = _snapshot_columns(report)
    out: list[dict] = []
    i = 0
    while i < len(src):
        col = src[i]
        if col.get("key") == "start" and i + 1 < len(src) and src[i + 1].get("key") == "end":
            out.append({"key": "__timerange", "label": "業務開始〜終了時間", "type": "timerange"})
            i += 2
            continue
        out.append(col)
        if col.get("key") == "date":
            out.append(_KIND_COLUMN)
        i += 1
    return out


def _kind_cell_value(line: dict) -> str:
    """種別セルの表示値。空行は空欄、勤務（記入あり）は「勤務」、有給/欠勤は短縮表記。"""
    kind = line.get("kind") or ""
    if kind == "paid_leave":
        return "有給"
    if kind == "absent":
        return "欠勤"
    has_data = any(str(value).strip() for key, value in line.items() if key != "kind")
    return "勤務" if has_data else ""


def _cell_text(line: dict, column: dict) -> str:
    """1セルの表示文字列。参照ビュー(report_view)の cell() と同じ表記にする。"""
    ctype = column.get("type")
    key = column.get("key")
    if ctype == "date":
        value = line.get(key) or ""
        wd = _weekday(value)
        return f"{value}（{wd}）" if value and wd else str(value)
    if ctype == "kind":
        return _kind_cell_value(line)
    if ctype == "timerange":
        start = line.get("start") or ""
        end = line.get("end") or ""
        return f"{start}〜{end}" if (start or end) else ""
    if ctype == "count_minutes":
        count = line.get(column.get("count_key"))
        minutes = line.get(column.get("minutes_key"))
        if count in (None, "") and minutes in (None, ""):
            return ""
        unit = column.get("unit") or "回"
        return f"{_int(count):,}{unit} / {_int(minutes):,}分"
    value = line.get(key)
    if ctype == "number":
        return "" if value in (None, "") else str(value)
    return str(value) if value is not None else ""


def _is_numeric_column(column: dict) -> bool:
    return column.get("type") in ("number", "count_minutes")


def _col_widths(display_cols: list[dict], mm, avail_width):
    """表示列(+先頭「回」列)の列幅。内容(note)を伸縮列とし、列が多い場合は比例縮小して収める。"""
    min_note = 28 * mm
    widths: list = [9 * mm]  # 先頭「回」列
    note_index = None
    for col in display_cols:
        key, ctype = col.get("key"), col.get("type")
        if key == "note":
            widths.append(None)
            note_index = len(widths) - 1
        elif ctype == "kind":
            widths.append(14 * mm)
        elif ctype == "timerange":
            widths.append(28 * mm)
        elif ctype == "count_minutes":
            widths.append(28 * mm)
        elif key == "date":
            widths.append(22 * mm)
        elif key == "subject_period":
            widths.append(16 * mm)
        elif ctype == "number":
            widths.append(16 * mm)
        else:
            widths.append(20 * mm)
    fixed_sum = sum(w for w in widths if w is not None)
    if note_index is not None:
        note_w = avail_width - fixed_sum
        if note_w < min_note:
            # 列が多すぎて入りきらない場合は固定列を比例縮小し、内容列を最小幅で確保する
            scale = max(0.5, (avail_width - min_note) / fixed_sum)
            widths = [(w * scale if w is not None else None) for w in widths]
            note_w = min_note
        widths[note_index] = note_w
    else:
        total = sum(widths)
        if total > avail_width:
            scale = avail_width / total
            widths = [w * scale for w in widths]
    return widths


def _summary_parts(report: WorkReport, lines: list[dict]) -> list[str]:
    """勤怠サマリ（勤務日数/有給/欠勤）＋集計可能列の合計。参照ビューの summary と同一。"""
    filtered = [line for line in lines if _has_data(line)]
    work_lines = [line for line in filtered if not is_leave_kind(line.get("kind"))]
    paid = sum(1 for line in filtered if line.get("kind") == "paid_leave")
    absent = sum(1 for line in filtered if line.get("kind") == "absent")
    parts = [
        f"勤務日数：{len(work_lines)}日",
        f"有給休暇：{paid}回",
        f"欠勤：{absent}回",
    ]
    # 集計はスナップショット列の summable 列のみ。勤務行（有給/欠勤を除く）で合計する。
    for col in _snapshot_columns(report):
        if not col.get("summable"):
            continue
        if col.get("type") == "count_minutes":
            cnt = sum(_int(line.get(col.get("count_key"))) for line in work_lines)
            mn = sum(_int(line.get(col.get("minutes_key"))) for line in work_lines)
            unit = col.get("unit") or "回"
            parts.append(f"{col.get('label')}：{cnt:,}{unit} / {mn:,}分")
        else:
            total = sum(_int(line.get(col.get("key"))) for line in work_lines)
            parts.append(f"{col.get('label')}：{total:,}")
    return parts


def _report_table_story(report: WorkReport, font_name: str, styles, cell_style, header_style) -> list:
    """1報告書分の明細テーブル＋勤怠サマリを返す（参照ビューと同一の列・値・集計）。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    lines: list[dict] = list((report.form_data or {}).get("lines", []))
    filtered = [line for line in lines if _has_data(line)]
    display_cols = _display_columns(report)

    header_cells = [Paragraph("回", header_style)]
    header_cells += [Paragraph(col.get("label", ""), header_style) for col in display_cols]
    rows = [header_cells]
    for index, line in enumerate(filtered, start=1):
        row = [str(index)]
        row += [_pdf_cell(col, _cell_text(line, col), cell_style) for col in display_cols]
        rows.append(row)

    style = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),  # 「回」列は中央寄せ
    ]
    if filtered:
        # 数値列（数値・採点）は右寄せ。先頭の「回」列ぶん +1 する。
        for col_index, col in enumerate(display_cols, start=1):
            if _is_numeric_column(col):
                style.append(("ALIGN", (col_index, 1), (col_index, -1), "RIGHT"))
    else:
        empty_row = [""] * (len(display_cols) + 1)
        empty_row[1 if display_cols else 0] = Paragraph("入力された指導日はありません。", cell_style)
        rows.append(empty_row)
        style.append(("SPAN", (1, 1), (-1, 1)))

    avail_width = landscape(A4)[0] - 24 * mm
    table = Table(rows, colWidths=_col_widths(display_cols, mm, avail_width), repeatRows=1)
    table.setStyle(TableStyle(style))
    summary = "　".join(_summary_parts(report, lines))
    return [table, Spacer(1, 2 * mm), Paragraph(summary, styles["Normal"])]


def _meta_line(report: WorkReport) -> str:
    """参照ビュー上部の補足情報（弊社担当・事業所の所在地・従事業務内容）を1行にまとめる。"""
    meta = (report.form_data or {}).get("meta") or {}
    parts = []
    for label, key in (
        ("弊社担当", "our_staff"),
        ("事業所の所在地", "dispatch_place_address"),
        ("従事業務内容", "work_content"),
    ):
        value = meta.get(key)
        if value not in (None, ""):
            parts.append(f"{label}：{value}")
    return "　".join(parts)


def _make_styles(font_name: str):
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    styles["Title"].fontSize = 14
    styles["Normal"].fontSize = 9
    cell_style = ParagraphStyle("cell", fontName=font_name, fontSize=8, leading=10)
    header_style = ParagraphStyle("cellhdr", fontName=font_name, fontSize=7, leading=8)
    return styles, cell_style, header_style


def build_report_pdf(report: WorkReport, student_name: str, tutor_name: str) -> bytes:
    """1報告書分のPDFバイト列を生成して返す。"""
    font_name = _register_font()

    year, month_str = report.target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"

    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc

    styles, cell_style, header_style = _make_styles(font_name)
    meta_line = _meta_line(report)

    # 動的列（担当業務・副業務・採点）で横に広くなるため横向きで出力する。
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), rightMargin=12 * mm, leftMargin=12 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title="指導実績")
    story = [
        Paragraph(f"指導実績　{student_name}　{tutor_name}　{month_label}", styles["Title"]),
        Spacer(1, 4 * mm),
    ]
    if meta_line:
        story.append(Paragraph(meta_line, styles["Normal"]))
        story.append(Spacer(1, 3 * mm))
    story.extend(_report_table_story(report, font_name, styles, cell_style, header_style))
    doc.build(story)
    return buf.getvalue()


def build_reports_pdf(reports: list[tuple[WorkReport, str, str]], target_month: str) -> bytes:
    """複数報告書分を1つのPDFにまとめて返す。"""
    font_name = _register_font()

    year, month_str = target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"

    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc

    styles, cell_style, header_style = _make_styles(font_name)

    # 動的列（担当業務・副業務・採点）で横に広くなるため横向きで出力する。
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="指導実績",
    )
    story = []

    for index, (report, student_name, tutor_name) in enumerate(reports):
        if index:
            story.append(PageBreak())
        meta_line = _meta_line(report)
        story.extend([
            Paragraph(f"指導実績　{student_name}　{tutor_name}　{month_label}", styles["Title"]),
            Spacer(1, 4 * mm),
        ])
        if meta_line:
            story.append(Paragraph(meta_line, styles["Normal"]))
            story.append(Spacer(1, 3 * mm))
        story.extend(_report_table_story(report, font_name, styles, cell_style, header_style))

    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CSV エクスポート（全講師分・横持ち＝位置固定／業務名はデータ）
# ---------------------------------------------------------------------------
# 担当業務は最大3・副業務は最大5・採点は1（契約モデルの上限）。位置を固定列にし、
# 業務名はセルの値として持つことで、講師ごとに業務がバラバラでも単一スキーマに収める。
_CSV_MAIN_RANGE = range(1, 4)   # 担当業務①〜③
_CSV_SUB_RANGE = range(1, 6)    # 副業務①〜⑤
_CSV_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]  # date.weekday(): 月=0
_TASK_LABEL_SUFFIX = re.compile(r"（[^（）]*）$")  # 「業務名（分）」「採点（回）」の末尾単位を除去


def _csv_weekday(date_str) -> str:
    try:
        return _CSV_WEEKDAYS[date.fromisoformat(str(date_str)).weekday()]
    except (TypeError, ValueError):
        return ""


def _clean_task_name(label) -> str:
    """列ラベル「数学指導（分）」「採点（回）」→ 業務名「数学指導」「採点」。"""
    return _TASK_LABEL_SUFFIX.sub("", str(label or "")).strip()


def _task_name_map(report: WorkReport):
    """報告書のスナップショット列定義から担当N/副N/採点の業務名を解決する。

    戻り値: (names: {列キー: 業務名}, scoring: (業務名, 回キー, 分キー)|None)
    """
    columns = ((report.form_data or {}).get("meta") or {}).get("column_definition") or []
    names: dict[str, str] = {}
    scoring = None
    for column in columns:
        if not isinstance(column, dict):
            continue
        key = column.get("key") or ""
        if key.startswith("task_minutes_") or key.startswith("sub_minutes_"):
            names[key] = _clean_task_name(column.get("label"))
        elif column.get("type") == "count_minutes" or key == "scoring":
            scoring = (
                _clean_task_name(column.get("label")),
                column.get("count_key") or "scoring_count",
                column.get("minutes_key") or "scoring_minutes",
            )
    return names, scoring


def _csv_value(value) -> str:
    return "" if value is None else str(value)


def _csv_header() -> list[str]:
    header = ["講師番号", "講師名", "派遣先", "お客様ID", "対象月",
              "日付", "曜日", "種別", "業務開始", "業務終了", "担当時限"]
    for i in _CSV_MAIN_RANGE:
        header += [f"担当業務{i}_名称", f"担当業務{i}_分"]
    for i in _CSV_SUB_RANGE:
        header += [f"副業務{i}_名称", f"副業務{i}_分"]
    header += ["採点_名称", "採点_回数", "採点_分", "休憩_分", "往復交通費_円", "内容"]
    return header


def build_reports_csv(reports: list[WorkReport], target_month: str) -> bytes:
    """全講師分の業務連絡表を1つのCSV（横持ち・位置固定）にする。Excel向けに UTF-8(BOM)。

    1行 = 講師 × 日 × 明細。担当①〜③/副①〜⑤/採点は固定列で、業務名はセルの値として持つ。
    未記入行は除外し、有給休暇・欠勤日は種別付きで1行出力する。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_csv_header())

    for report in reports:
        meta = (report.form_data or {}).get("meta") or {}
        names, scoring = _task_name_map(report)
        main_names = [names.get(f"task_minutes_{i}", "") for i in _CSV_MAIN_RANGE]
        sub_names = [names.get(f"sub_minutes_{i}", "") for i in _CSV_SUB_RANGE]
        # 旧データ（動的列スナップショット無し）の救済: 静的 teach_minutes を担当①に寄せる
        legacy = not any(main_names) and not any(sub_names) and scoring is None
        if legacy:
            main_names[0] = main_names[0] or "数学科指導"
        base = [
            _csv_value(report.tutor_no),
            _csv_value(report.tutor_name),
            _csv_value(report.school_name),
            _csv_value(meta.get("customer_id")),
            _csv_value(report.target_month),
        ]
        for line in (report.form_data or {}).get("lines") or []:
            if not isinstance(line, dict):
                continue
            if not any(str(value).strip() for value in line.values()):
                continue  # 未記入行は出力しない
            row = list(base)
            row += [
                _csv_value(line.get("date")),
                _csv_weekday(line.get("date")),
                ATTENDANCE_LABELS.get(line.get("kind") or "", "勤務"),
                _csv_value(line.get("start")),
                _csv_value(line.get("end")),
                _csv_value(line.get("subject_period")),
            ]
            for index, i in enumerate(_CSV_MAIN_RANGE):
                value = line.get(f"task_minutes_{i}", "")
                if index == 0 and legacy and (value == "" or value is None):
                    value = line.get("teach_minutes", "")
                row += [main_names[index], _csv_value(value)]
            for index, i in enumerate(_CSV_SUB_RANGE):
                row += [sub_names[index], _csv_value(line.get(f"sub_minutes_{i}", ""))]
            if scoring:
                name, count_key, minutes_key = scoring
                row += [name, _csv_value(line.get(count_key, "")), _csv_value(line.get(minutes_key, ""))]
            else:
                row += ["", "", ""]
            row += [
                _csv_value(line.get("break_minutes")),
                _csv_value(line.get("commute_fee")),
                _csv_value(line.get("note")),
            ]
            writer.writerow(row)

    return buf.getvalue().encode("utf-8-sig")
