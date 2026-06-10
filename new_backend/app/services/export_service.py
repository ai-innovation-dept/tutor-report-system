"""PDFエクスポートサービス（monthly_dispatch フォーム対応）。"""
import io
import os
from datetime import datetime

from app.forms.definitions import get_form
from app.models.work import WorkReport

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


def build_report_pdf(report: WorkReport, student_name: str, tutor_name: str) -> bytes:
    """1報告書分のPDFバイト列を生成して返す。"""
    font_name = _register_font()
    form = get_form(report.form_type)
    lines: list[dict] = report.form_data.get("lines", [])
    header: dict = report.form_data.get("header", {})

    year, month_str = report.target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc

    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    styles["Title"].fontSize = 14
    styles["Normal"].fontSize = 9
    cell_style = ParagraphStyle("cell", fontName=font_name, fontSize=8, leading=10)
    header_style = ParagraphStyle("cellhdr", fontName=font_name, fontSize=7, leading=8)

    col_widths = [22 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 22 * mm, None]
    col_labels = [Paragraph(c.label, header_style) for c in form.columns]

    rows = [col_labels]
    for line in lines:
        rows.append([_pdf_cell(c.key, line.get(c.key, ""), cell_style) for c in form.columns])

    # 合計行
    totals = ["合計", "", "", "", "", "", ""]
    for key in form.summable_keys:
        col_index = next((i for i, c in enumerate(form.columns) if c.key == key), None)
        if col_index is not None:
            try:
                total = sum(int(line.get(key, 0) or 0) for line in lines)
                totals[col_index] = str(total)
            except (ValueError, TypeError):
                pass
    # 合計行の最後の列は空
    while len(totals) < len(form.columns):
        totals.append("")
    rows.append(totals)

    avail_width = A4[0] - 32 * mm
    filled = sum(w for w in col_widths if w is not None)
    col_widths[-1] = avail_width - filled
    table = Table(rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7f7f7")),
    ]))

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
    story.append(table)
    doc.build(story)
    return buf.getvalue()


def build_reports_pdf(reports: list[tuple[WorkReport, str, str]], target_month: str) -> bytes:
    """複数報告書分を1つのPDFにまとめて返す。"""
    font_name = _register_font()

    year, month_str = target_month.split("-")
    month_label = f"{year}年{int(month_str):02d}月"

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ModuleNotFoundError as exc:
        raise RuntimeError("reportlab is not installed") from exc

    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = font_name
    styles["Title"].fontSize = 14
    styles["Normal"].fontSize = 9
    cell_style = ParagraphStyle("cell", fontName=font_name, fontSize=8, leading=10)
    header_style = ParagraphStyle("cellhdr", fontName=font_name, fontSize=7, leading=8)

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

        form = get_form(report.form_type)
        lines: list[dict] = (report.form_data or {}).get("lines", [])
        header: dict = (report.form_data or {}).get("header", {})
        col_widths = [22 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 22 * mm, None]
        col_labels = [Paragraph(c.label, header_style) for c in form.columns]

        rows = [col_labels]
        for line in lines:
            rows.append([_pdf_cell(c.key, line.get(c.key, ""), cell_style) for c in form.columns])

        totals = ["合計", "", "", "", "", "", ""]
        for key in form.summable_keys:
            col_index = next((i for i, c in enumerate(form.columns) if c.key == key), None)
            if col_index is not None:
                try:
                    totals[col_index] = str(sum(int(line.get(key, 0) or 0) for line in lines))
                except (ValueError, TypeError):
                    pass
        while len(totals) < len(form.columns):
            totals.append("")
        rows.append(totals)

        avail_width = A4[0] - 32 * mm
        filled = sum(w for w in col_widths if w is not None)
        col_widths[-1] = avail_width - filled
        table = Table(rows, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#777777")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f7f7f7")),
        ]))

        story.extend([
            Paragraph(f"指導実績　{student_name}　{tutor_name}　{month_label}", styles["Title"]),
            Spacer(1, 4 * mm),
        ])
        if header:
            header_text = "　".join(f"{k}：{v}" for k, v in header.items())
            story.append(Paragraph(header_text, styles["Normal"]))
            story.append(Spacer(1, 3 * mm))
        story.append(table)

    doc.build(story)
    return buf.getvalue()
