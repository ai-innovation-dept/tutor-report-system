"""PDF / CSV エクスポートサービス（monthly_dispatch フォーム対応）。"""
import csv
import io
import os
import re
from datetime import date, datetime

from app.forms.definitions import get_form
from app.models.work import WorkReport
from app.services.report_service import ATTENDANCE_LABELS, attendance_counts, is_leave_kind

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


def _pdf_cell(column_key: str, value, cell_style):
    """セル値を返す。折り返し対象列は Paragraph（XMLエスケープ＋改行→<br/>）にして枠内に収める。"""
    from xml.sax.saxutils import escape
    from reportlab.platypus import Paragraph

    text = str(value if value is not None else "")
    if column_key in _PDF_WRAP_KEYS and text.strip():
        return Paragraph(escape(text).replace("\n", "<br/>"), cell_style)
    return text


def _display_columns(form) -> list[tuple[str, str]]:
    """フォーム列に勤怠区分（種別）を日付の直後へ差し込んだ表示列 [(key, label), ...] を返す。"""
    columns: list[tuple[str, str]] = []
    for column in form.columns:
        columns.append((column.key, column.label))
        if column.key == "date":
            columns.append(("kind", "種別"))
    return columns


def _kind_cell_value(line: dict) -> str:
    """種別セルの表示値。空行は空欄、勤務（記入あり）は「勤務」、有給/欠勤は短縮表記。"""
    kind = line.get("kind") or ""
    if kind == "paid_leave":
        return "有給"
    if kind == "absent":
        return "欠勤"
    has_data = any(str(value).strip() for key, value in line.items() if key != "kind")
    return "勤務" if has_data else ""


def _col_widths(display_cols: list[tuple[str, str]], mm, avail_width):
    """表示列に応じた列幅。内容(note)列を伸縮列とし、残り幅を割り当てる。"""
    widths = []
    for key, _ in display_cols:
        if key == "note":
            widths.append(None)
        elif key == "kind":
            widths.append(14 * mm)
        elif key == "date":
            widths.append(20 * mm)
        else:
            widths.append(17 * mm)
    if None in widths:
        filled = sum(w for w in widths if w is not None)
        widths[widths.index(None)] = max(20 * mm, avail_width - filled)
    return widths


def _attendance_summary_text(lines: list[dict]) -> str:
    counts = attendance_counts(lines)
    return (
        f"勤務日数：{counts['work_days']}日　"
        f"有給休暇：{counts['paid_leave']}回　欠勤：{counts['absent']}回"
    )


def _report_table_story(report: WorkReport, font_name: str, styles, cell_style, header_style) -> list:
    """1報告書分の明細テーブル＋勤怠サマリ行（種別・取得回数・欠勤回数）を返す。"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    form = get_form(report.form_type)
    lines: list[dict] = list((report.form_data or {}).get("lines", []))
    display_cols = _display_columns(form)

    rows = [[Paragraph(label, header_style) for _, label in display_cols]]
    for line in lines:
        rows.append([
            _pdf_cell(key, _kind_cell_value(line) if key == "kind" else line.get(key, ""), cell_style)
            for key, _ in display_cols
        ])

    # 合計行（勤務行のみ集計。有給/欠勤行は勤務時間を持たない）
    totals = [""] * len(display_cols)
    totals[0] = "合計"
    for key in form.summable_keys:
        col_index = next((i for i, (k, _) in enumerate(display_cols) if k == key), None)
        if col_index is not None:
            try:
                totals[col_index] = str(
                    sum(int(line.get(key, 0) or 0) for line in lines if not is_leave_kind(line.get("kind")))
                )
            except (ValueError, TypeError):
                pass
    rows.append(totals)

    avail_width = A4[0] - 32 * mm
    table = Table(rows, colWidths=_col_widths(display_cols, mm, avail_width), repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7f7f7")),
    ]))
    return [table, Spacer(1, 2 * mm), Paragraph(_attendance_summary_text(lines), styles["Normal"])]


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
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc

    styles, cell_style, header_style = _make_styles(font_name)
    header: dict = report.form_data.get("header", {})

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
                            topMargin=20 * mm, bottomMargin=20 * mm, title="指導実績")
    story = [
        Paragraph(f"指導実績　{student_name}　{tutor_name}　{month_label}", styles["Title"]),
        Spacer(1, 4 * mm),
    ]
    if header:
        header_text = "　".join(f"{k}：{v}" for k, v in header.items())
        story.append(Paragraph(header_text, styles["Normal"]))
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
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc

    styles, cell_style, header_style = _make_styles(font_name)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        title="指導実績",
    )
    story = []

    for index, (report, student_name, tutor_name) in enumerate(reports):
        if index:
            story.append(PageBreak())
        header: dict = (report.form_data or {}).get("header", {})
        story.extend([
            Paragraph(f"指導実績　{student_name}　{tutor_name}　{month_label}", styles["Title"]),
            Spacer(1, 4 * mm),
        ])
        if header:
            header_text = "　".join(f"{k}：{v}" for k, v in header.items())
            story.append(Paragraph(header_text, styles["Normal"]))
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
