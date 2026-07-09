# === PDF共通: 日本語フォント登録 START ===
"""PDF出力用の日本語フォント登録。

「指導時間確認票」(api/reports.py) と「指導日報」(services/daily_report_pdf.py) の
両PDFで同一フォントを共用するための共通モジュール。登録は プロセス内で1回のみ。
"""
import os

from fastapi import HTTPException

PDF_FONT_NAME = "JapaneseReportFont"
_registered = False


def _pdf_font_paths() -> list[str]:
    return [
        os.environ.get("PDF_JP_FONT_PATH", ""),
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/NotoSansJP-VF.ttf",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
    ]


def register_pdf_font() -> str:
    """日本語TTFをreportlabへ登録しフォント名を返す。見つからなければ500。"""
    global _registered
    if _registered:
        return PDF_FONT_NAME
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail="reportlab is not installed") from exc

    for path in _pdf_font_paths():
        if path and os.path.exists(path):
            try:
                pdfmetrics.registerFont(TTFont(PDF_FONT_NAME, path))
                _registered = True
                return PDF_FONT_NAME
            except Exception:
                continue
    raise HTTPException(status_code=500, detail="Japanese PDF font is not installed")
# === PDF共通: 日本語フォント登録 END ===
