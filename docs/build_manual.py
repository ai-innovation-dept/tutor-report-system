# -*- coding: utf-8 -*-
"""指導実績報告システム 操作手順書（PDF）を生成するスクリプト。
日本語フォント(IPAゴシック)とreportlabが入ったbackendコンテナ内で実行する想定。
  例) docker compose cp docs/build_manual.py backend:/tmp/build_manual.py
      docker compose exec backend python /tmp/build_manual.py /tmp/操作手順書.pdf
"""
import os
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, ListFlowable, ListItem, PageBreak, Paragraph,
    SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.graphics.shapes import Drawing, Polygon, Rect, String

FONT_CANDIDATES = [
    os.environ.get("PDF_JP_FONT_PATH", ""),
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
]
FONT = "JP"
for p in FONT_CANDIDATES:
    if p and os.path.isfile(p):
        pdfmetrics.registerFont(TTFont(FONT, p))
        break
else:
    raise SystemExit("Japanese font not found")

# ---- 色 ----
NAVY = colors.HexColor("#1f3a5f")
BLUE = colors.HexColor("#2563eb")
LIGHTBLUE = colors.HexColor("#eef4ff")
AMBER = colors.HexColor("#b45309")
AMBERBG = colors.HexColor("#fff7ed")
GREEN = colors.HexColor("#15803d")
GREENBG = colors.HexColor("#ecfdf5")
REDBG = colors.HexColor("#fef2f2")
RED = colors.HexColor("#b91c1c")
GREY = colors.HexColor("#475569")
LINE = colors.HexColor("#cbd5e1")

# ---- スタイル ----
title = ParagraphStyle("title", fontName=FONT, fontSize=24, leading=32, textColor=NAVY, alignment=TA_CENTER)
subtitle = ParagraphStyle("subtitle", fontName=FONT, fontSize=12, leading=18, textColor=GREY, alignment=TA_CENTER)
h1 = ParagraphStyle("h1", fontName=FONT, fontSize=15, leading=22, textColor=colors.white)
h2 = ParagraphStyle("h2", fontName=FONT, fontSize=12.5, leading=20, textColor=NAVY, spaceBefore=8, spaceAfter=2)
body = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=17, textColor=colors.HexColor("#1f2937"))
small = ParagraphStyle("small", fontName=FONT, fontSize=9, leading=14, textColor=GREY)
cell = ParagraphStyle("cell", fontName=FONT, fontSize=9.5, leading=14, textColor=colors.HexColor("#1f2937"))
cellb = ParagraphStyle("cellb", fontName=FONT, fontSize=9.5, leading=14, textColor=NAVY)
stepnum = ParagraphStyle("stepnum", fontName=FONT, fontSize=12, leading=14, textColor=colors.white, alignment=TA_CENTER)
boxhead = ParagraphStyle("boxhead", fontName=FONT, fontSize=10.5, leading=15, textColor=colors.white)

story = []


def section(no, text):
    t = Table([[Paragraph(f"{no}", ParagraphStyle("sn", fontName=FONT, fontSize=15, textColor=colors.white, alignment=TA_CENTER)),
                Paragraph(text, h1)]], colWidths=[12 * mm, None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), BLUE),
        ("BACKGROUND", (1, 0), (1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(Spacer(1, 4 * mm))
    story.append(t)
    story.append(Spacer(1, 3 * mm))


def steps(items):
    rows = []
    for i, it in enumerate(items, 1):
        rows.append([Paragraph(str(i), stepnum), Paragraph(it, cell)])
    t = Table(rows, colWidths=[9 * mm, None])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (1, 0), (1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
    ]
    for r in range(len(items)):
        style.append(("BACKGROUND", (0, r), (0, r), BLUE))
        style.append(("ROUNDEDCORNERS", [2, 2, 2, 2]))
    t.setStyle(TableStyle(style))
    story.append(t)


def callout(kind, text):
    bg = {"hint": LIGHTBLUE, "caution": AMBERBG, "ok": GREENBG, "rule": REDBG}[kind]
    bar = {"hint": BLUE, "caution": AMBER, "ok": GREEN, "rule": RED}[kind]
    label = {"hint": "ヒント", "caution": "注意", "ok": "ポイント", "rule": "重要"}[kind]
    inner = Table([[Paragraph(f"<b>{label}</b>", ParagraphStyle("cl", fontName=FONT, fontSize=10, textColor=bar)),
                    Paragraph(text, body)]], colWidths=[18 * mm, None])
    inner.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    t = Table([[inner]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LINEBEFORE", (0, 0), (0, -1), 3, bar),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(Spacer(1, 2 * mm))
    story.append(t)
    story.append(Spacer(1, 2 * mm))


def para(text, style=body):
    story.append(Paragraph(text, style))


def info_table(rows, headers, widths):
    data = [[Paragraph(h, cellb) for h in headers]] + [[Paragraph(c, cell) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(t)


def flow_diagram():
    """承認の流れ（縦）を箱と矢印で描く。"""
    steps_ = [
        ("講師", "報告書を作成する", BLUE),
        ("講師", "保護者へ承認依頼", BLUE),
        ("保護者", "内容を確認して承認", GREEN),
        ("受付", "受付承認（修正可）", NAVY),
        ("再鑑", "再鑑承認", NAVY),
        ("管理者", "最終承認 → 完了！", NAVY),
    ]
    box_w, box_h, gap = 120, 24, 16
    width = 330
    height = len(steps_) * box_h + (len(steps_) - 1) * gap + 10
    d = Drawing(width, height)
    y = height - box_h
    cx = 90
    for i, (who, what, col) in enumerate(steps_):
        d.add(Rect(cx, y, box_w, box_h, rx=5, ry=5, fillColor=colors.Color(col.red, col.green, col.blue, 0.10), strokeColor=col, strokeWidth=1))
        d.add(String(cx + 8, y + box_h - 11, who, fontName=FONT, fontSize=9, fillColor=col))
        d.add(String(cx + 8, y + 6, what, fontName=FONT, fontSize=8.5, fillColor=colors.HexColor("#1f2937")))
        if i < len(steps_) - 1:
            ax = cx + box_w / 2
            d.add(Polygon([ax - 5, y - 3, ax + 5, y - 3, ax, y - gap + 3], fillColor=BLUE, strokeColor=BLUE))
        y -= (box_h + gap)
    # 差戻しの注記（右側）
    d.add(String(cx + box_w + 8, height / 2, "← どの段階でも", fontName=FONT, fontSize=8, fillColor=RED))
    d.add(String(cx + box_w + 8, height / 2 - 12, "「差戻し」で前へ", fontName=FONT, fontSize=8, fillColor=RED))
    d.add(String(cx + box_w + 8, height / 2 - 24, "戻せます", fontName=FONT, fontSize=8, fillColor=RED))
    d.hAlign = "CENTER"
    story.append(d)


def screen_sketch(title_text, lines):
    """簡単な画面イメージ（枠＋行）"""
    inner = [[Paragraph(f"<b>{title_text}</b>", ParagraphStyle("st", fontName=FONT, fontSize=9, textColor=colors.white))]]
    head = Table(inner, colWidths=[None])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GREY), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    rows = [[head]] + [[Paragraph(l, small)] for l in lines]
    t = Table(rows, colWidths=[None])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, GREY),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, LINE),
        ("LEFTPADDING", (0, 1), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4), ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
    ]))
    story.append(t)


# ============ 表紙 ============
story.append(Spacer(1, 40 * mm))
para("指導実績報告システム", title)
story.append(Spacer(1, 4 * mm))
para("操作手順書（プロトタイプお試し版）", subtitle)
story.append(Spacer(1, 2 * mm))
para("はじめての方でも、この手順どおりに進めれば一人で操作できます。", subtitle)
story.append(Spacer(1, 14 * mm))
flow_diagram()
story.append(PageBreak())

# ============ はじめに ============
section("0", "はじめに（このシステムでできること）")
para("先生（講師）が毎月の指導内容を記録し、保護者と運営スタッフが順番に確認・承認していくシステムです。"
     "登場する人（役割）と、全体の流れは下のとおりです。")
story.append(Spacer(1, 2 * mm))
para("● 登場する人（役割）", h2)
info_table(
    [
        ["管理者／管理責任者", "利用者（アカウント）の作成や、講師・保護者・生徒の紐づけを管理します。"],
        ["講師", "指導した内容の報告書を作成し、保護者へ承認をお願いします。"],
        ["保護者", "わが子の報告書を確認し、承認（または差戻し）します。"],
        ["受付 → 再鑑 → 管理者", "運営スタッフが3段階で内容を確認・承認します。修正できるのは受付だけです。"],
    ],
    ["役割", "おもな役目"], [40 * mm, None],
)
callout("hint", "自分がどの役割かは、ログイン後に画面左のメニュー（ダッシュボード／報告書一覧／承認管理 など）で分かります。")

para("● ログインのしかた（全員共通）", h2)
steps([
    "ブラウザ（Chrome など）で、ご案内された URL を開きます（例：http://52.197.43.164:8000）。",
    "「メールアドレス」と「パスワード」を入力します。",
    "「ログイン」ボタンを押します。",
])
callout("hint", "お試し用のアカウントは、巻末の「デモ用アカウント一覧」をご覧ください（パスワードは共通で <b>Passw0rd!</b>）。")
story.append(PageBreak())

# ============ 1. ユーザ作成 ============
section("1", "ユーザ（アカウント）を作成する　※管理者の操作")
para("新しく講師・保護者・運営スタッフを使えるようにするには、まず管理者がアカウントを「招待」します。")
steps([
    "管理者でログインし、左メニューの「ユーザー管理」を開きます。",
    "「新規ユーザー登録」で、作りたい役割（講師／保護者／受付／再鑑／管理者）を選びます。",
    "講師・運営スタッフ → 「氏名」と「メールアドレス」を入力します。",
    "保護者 → 「担当講師」を選び、「生徒名」と「メールアドレス」を入力します。",
    "「招待メールを送る」を押すと、相手に登録用のメールが届きます。",
    "メールを受け取った本人がリンクを開き、パスワードを設定すると登録完了です。",
])
callout("caution", "招待リンクの有効期限は <b>72時間</b> です。期限が切れたら、一覧の「再送」でもう一度送れます。")
callout("hint", "お試し環境では、送信されたメールは「メール確認画面（MailHog）」で確認できる場合があります（例：http://52.197.43.164:8025）。")

para("● ユーザー詳細画面の見かた（項目の説明）", h2)
para("登録済みユーザー一覧の「詳細」ボタンを押すと、その人の情報を確認・操作できます。各項目の意味は次のとおりです。")
info_table(
    [
        ["基本情報", "No（利用者番号）・名前・メール・登録日・最終ログイン日を表示します。"],
        ["ロール設定", "役割を表示します。受付／再鑑は両方を兼ねる設定をチェックで切り替え、「保存」で確定します。"],
        ["担当生徒（講師の場合）", "その講師が担当する生徒の一覧を表示します。"],
        ["紐づく生徒（保護者の場合）", "その保護者に紐づく生徒の一覧。下の欄から「保護者未設定の生徒」を選んで紐づけられます。"],
        ["保護者承認をスキップ（管理責任者のみ）", "ONにすると、その保護者の承認を省略して直接運営へ提出します。"],
        ["状態管理", "「無効化する／有効化する」で、その人の利用可否を切り替えます。"],
        ["危険な操作（削除）", "ユーザーを完全に削除します。誤操作防止のため確認用にメールアドレスの入力が必要です（元に戻せません）。"],
    ],
    ["項目", "説明"], [54 * mm, None],
)
story.append(PageBreak())

# ============ 2. 紐づき設定 ============
section("2", "紐づき設定（講師・保護者・生徒）　※管理者の操作")
para("「誰の保護者の、どの生徒を、どの講師が担当するか」を結びつける設定です。"
     "この紐づけができていないと、講師は承認依頼ができません。")

para("● いちばん基本の流れ", h2)
steps([
    "手順1で保護者を招待するとき、「担当講師」と「生徒名」を入力します。",
    "保護者が招待メールから登録を完了すると、その生徒と保護者・講師が自動でつながります。",
])
callout("ok", "同じ保護者にもう一人お子さんを追加するときも、同じ手順（担当講師＋生徒名を入力して招待）でOKです。")

para("● 「保護者が未設定」の生徒に保護者をつける", h2)
para("先に生徒だけ登録されていて保護者が決まっていない場合は、後からつなげられます。")
steps([
    "「ユーザー管理」で対象の保護者の「詳細」を開きます。",
    "「紐づく生徒」の欄にある「保護者未設定の生徒」から、対象の生徒を選びます。",
    "「紐づける」を押すと、その生徒に保護者が設定されます。",
])

para("● 担当を変更・整理したいとき（担当管理）", h2)
para("左メニューの「担当管理」では、一覧から各担当を編集できます。")
info_table(
    [
        ["講師を交代する", "担当の講師を別の先生に変更します（過去の報告書の記録は残ります）。"],
        ["保護者を変更する", "間違えてつけた保護者を正しい保護者に直します。"],
        ["生徒名を直す", "名前の打ち間違いなどを修正します。"],
        ["有効／無効", "使わなくなった担当を無効にします。"],
        ["＋担当を追加", "新しい担当（同じ生徒に別の講師、など）を作ります。"],
    ],
    ["できること", "説明"], [38 * mm, None],
)
screen_sketch("担当管理 画面（イメージ）", [
    "[ 生徒名・講師名・保護者名で検索 ]　 □ 有効のみ表示　 ［＋ 担当を追加］",
    "生徒名　　 講師　　　 保護者　　　 状態　 操作",
    "生徒1　　 講師 一郎　 保護者 一郎　 有効　 ［編集］",
    "二郎　　　 講師 二郎　 未設定（黄色） 有効　 ［編集］",
])
para("「編集」を押すと、次の項目を変更できます。", body)
info_table(
    [
        ["生徒名", "名前の打ち間違いなどを修正します（過去の報告書はそのまま残ります）。"],
        ["講師", "担当の先生を交代します。"],
        ["保護者", "保護者を選び直します。「未設定」を選ぶと紐づけを解除します。"],
        ["有効", "チェックを外すと、その担当を無効（非表示扱い）にします。"],
    ],
    ["編集できる項目", "説明"], [40 * mm, None],
)
callout("hint", "迷ったら「担当管理」を見れば、いま誰がどの生徒を担当し、保護者がついているかが一目で分かります（保護者が「未設定」は黄色で表示されます）。")
story.append(PageBreak())

# ============ 3. 報告書作成 ============
section("3", "講師が報告書を作成する　※講師の操作")
steps([
    "講師でログインし、左メニューの「報告書一覧」を開きます。",
    "「簡易作成」で、まず対象の「生徒」を選びます。",
    "「指導日」を選びます（日付を選ぶと曜日が自動で表示されます）。",
    "「開始時刻」「終了時刻」を選びます（5分単位で選べます）。",
    "「休憩等の時間（分）」を入れます（5分単位）。",
    "「指導時間数」は自動で計算されます。0.5時間（30分）単位になるよう、休憩で調整してください。",
    "「科目」「指導内容」を入力し、「保存」を押します。",
])
screen_sketch("簡易作成（イメージ）", [
    "生徒：[ 生徒を選択 ▼ ]",
    "指導日：[ 2026/06/10 ] （水）",
    "開始時刻：[ 16:00 ] 〜 終了時刻：[ 18:00 ]",
    "休憩等の時間（分）：[ 0 ]",
    "指導時間数：2時間（自動計算）",
    "科目：[ 数学 ]　指導内容：[ ……… ]",
    "［ 保存 ］",
])
callout("caution", "指導時間数が0.5時間（30分）単位になっていないと保存できません。休憩時間で調整してください。")
story.append(PageBreak())

# ============ 4. 承認依頼 ============
section("4", "保護者へ承認依頼する　※講師の操作")
steps([
    "講師でログインし、左メニューの「承認管理」を開きます。",
    "当月の報告書がまとまって表示されます。内容を確認します。",
    "「保護者へまとめて承認依頼」を押すと、保護者に確認のお願いが届きます。",
])
callout("caution", "「保護者が未設定のため承認依頼できません」と出たら、まだ生徒に保護者がついていません。"
        "手順2（紐づき設定）で保護者をつないでから、もう一度お試しください。")
callout("hint", "差戻し（修正のお願い）された場合は、報告書を直したあと「修正済みの報告書を保護者へまとめて再依頼」で再度お願いできます。")
story.append(PageBreak())

# ============ 5. 確認・修正・承認・差戻し ============
section("5", "確認・承認・差戻し（保護者／運営）")
para("● 保護者の操作（承認・差戻し）", h2)
steps([
    "保護者でログインし、左メニューの「承認管理」を開きます。",
    "「報告書を確認」で内容を見ます。",
    "問題なければ「すべて承認する」を押します。確認画面で「承認は保護者本人が操作しています」にチェックを入れてから承認します。",
    "直してほしいときは「差戻す」を押し、理由（コメント）を入力して差し戻します。",
])
callout("caution", "差戻しのときは、理由（コメント）の入力が必須です。コメントは講師に通知されます。")

para("● ダッシュボードの見かた（運営）", h2)
para("運営スタッフは「ダッシュボード」で報告書を確認・承認します。画面の構成は次のとおりです。")
info_table(
    [
        ["上部のしぼり込み", "「対象月」や「講師」で表示をしぼり込めます。"],
        ["状況の列", "報告書が今どの段階か（受付待ち／再鑑待ち／最終承認待ち／完了 など）が列で分かれて並びます。"],
        ["自分の担当タスク", "自分が対応する段階の報告書に、承認ボタンと「差戻し」が表示されます。"],
        ["報告書を確認", "指導日・時間・科目などの内容を別画面で確認できます。"],
        ["承認／差戻し", "承認ボタン（受付承認／再鑑承認／最終承認）または「差戻し」を押します。"],
    ],
    ["画面の要素", "説明"], [40 * mm, None],
)
callout("caution", "公正のため、自分が「受付」した報告書は、同じ人が「再鑑」できません（ボタンが押せない状態になります）。")

para("● 運営スタッフの操作（受付 → 再鑑 → 管理者）", h2)
steps([
    "運営スタッフでログインし、左メニューの「ダッシュボード」を開きます。",
    "自分の担当（受付なら『受付待ち』など）の報告書が表示されます。「報告書を確認」で内容を見ます。",
    "問題なければ承認ボタン（受付承認／再鑑承認／最終承認）を押します。",
    "直してほしいときは「差戻し」を押し、理由を入力して差し戻します。",
])
para("● 報告書の修正は「受付」だけができます", h2)
steps([
    "受付でログインし、ダッシュボードで対象の報告書を開きます。",
    "「報告書の修正（受付）」で内容を直します。",
    "「修正して通知」を押すと、修正内容が講師・保護者にメールで通知されます。",
])
callout("ok", "承認の順番は「講師 → 保護者 → 受付 → 再鑑 → 管理者（最終）」。最後の管理者まで承認されると完了です。")
story.append(PageBreak())

# ============ 6. 困ったとき ============
section("6", "困ったとき（よくある質問）")
info_table(
    [
        ["ログインできない", "メールアドレス・パスワードを確認してください。パスワードを忘れたら、ログイン画面の「パスワードをお忘れの方はこちら」から再設定できます。"],
        ["承認依頼ボタンが押せない／出ない", "生徒に保護者がついていない可能性があります。手順2で紐づけてください。また、当月分のみ操作できます。"],
        ["保存できない（報告書）", "指導時間数が0.5時間（30分）単位になっていません。休憩時間で調整してください。"],
        ["差戻しできない", "差戻しには理由（コメント）の入力が必要です。"],
        ["同じ人が受付と再鑑を両方できない", "公正のため、同じ報告書では受付した人は再鑑できません（その逆も同様）。別の担当者が操作してください。"],
        ["ログアウトしたい", "画面右上（またはメニュー）のログアウトから終了できます。"],
    ],
    ["こんなとき", "対処"], [50 * mm, None],
)
story.append(Spacer(1, 3 * mm))

# ============ 7. ルール ============
section("7", "当システムのルール（大切な約束）")
rules = [
    "承認は決まった順番で進みます：講師 → 保護者 → 受付 → 再鑑 → 管理者（最終承認）。",
    "差戻し（修正のお願い）には、必ず理由（コメント）を入力します。差し戻すと一つ前の人へ戻ります。",
    "生徒に保護者がついていないと、講師は承認依頼ができません（先に紐づけが必要）。",
    "報告書の時刻は5分単位、指導時間数は0.5時間（30分）単位で記録します。",
    "報告書の内容を修正できるのは「受付」だけです（修正すると講師・保護者へ通知されます）。",
    "公正のため、同じ報告書で「受付」と「再鑑」を同じ人が兼ねることはできません。",
    "操作できるのは当月分の報告書です（過去月は参照のみ）。",
    "招待メールのリンクは72時間で期限切れになります。",
]
story.append(ListFlowable(
    [ListItem(Paragraph(r, body), leftIndent=6, value=i + 1) for i, r in enumerate(rules)],
    bulletType="1", bulletFontName=FONT, bulletFormat="%s.", leftIndent=14,
))
story.append(Spacer(1, 4 * mm))

# ============ 付録：デモアカウント ============
para("● デモ用アカウント一覧（お試し用）", h2)
info_table(
    [
        ["管理者", "master1@example.com", "ユーザ作成・紐づけ・最終承認"],
        ["講師", "tutor1@example.com", "報告書の作成・承認依頼"],
        ["保護者", "parent1@example.com", "報告書の承認・差戻し"],
        ["受付", "receiver1@example.com", "受付承認・修正・差戻し"],
        ["再鑑", "reviewer1@example.com", "再鑑承認・差戻し"],
    ],
    ["役割", "メールアドレス", "できること"], [26 * mm, 70 * mm, None],
)
para("※ パスワードはすべて共通：<b>Passw0rd!</b>（お試し環境用）", small)
story.append(Spacer(1, 3 * mm))
callout("hint", "おすすめのお試し順：①管理者でログイン→紐づけ確認 → ②講師で報告書作成→承認依頼 → "
        "③保護者で承認 → ④受付→再鑑→管理者の順に承認、で「完了」まで体験できます。")


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 8)
    canvas.setFillColor(GREY)
    canvas.drawString(20 * mm, 12 * mm, "指導実績報告システム 操作手順書")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"- {doc.page} -")
    canvas.setStrokeColor(LINE)
    canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
    canvas.restoreState()


out = sys.argv[1] if len(sys.argv) > 1 else "操作手順書.pdf"
doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                        topMargin=18 * mm, bottomMargin=20 * mm, title="指導実績報告システム 操作手順書")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote", out)
