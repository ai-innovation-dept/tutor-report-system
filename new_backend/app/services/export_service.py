"""PDFエクスポートサービス（monthly_dispatch フォーム対応）。"""
import io
import os
from datetime import datetime

from app.forms.definitions import get_form
from app.models.work import WorkReport
from app.services.report_service import attendance_counts, is_leave_kind

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
