# === 改修 202607231933 の画面文字列テスト START ===
"""改修 202607231933 のUI検証（配信HTMLの文字列レベル。描画は client JS のため既存UIテストと同方式）。

- ① 保護者の参照画面: 承認フォーム表示中は指導月報カード側の参照用「保護者記入欄」を出さない
     （同名欄の二重表示を防ぐガード parentNoteFormVisible を入力フォーム側・参照側の両方が参照する）
- ② /admin/surveys ダッシュボード再設計（設問カードのグリッド化・講師別テーブルの並べ替え・
     注意ハイライト・全体平均ベンチマーク・絞り込みクリア・帯グラフの伸び切り防止）
"""
from tests.conftest import token


def test_parent_note_shown_only_once_guard(client, db):
    token(client, "parent@example.com")
    html = client.get("/parent/report-view?month=2026-07").text
    # 二重表示防止ガードが存在し、入力フォーム側と月報カード（参照）側の両方が同じ判定を使う
    assert "parentNoteFormVisible" in html
    assert html.count("parentNoteFormVisible()") >= 2
    # 参照用の欄自体は残っている（承認フォームが出ない状態＝承認後・運営の参照では引き続き表示）
    assert "保護者記入欄（ご要望／連絡事項）" in html


def test_admin_surveys_dashboard_layout(client, db):
    token(client, "receiver@example.com")
    html = client.get("/admin/surveys").text
    # 設問別の回答分布はコンパクトなカードのグリッド（横長プログレスバーの廃止）
    assert 'id="questionCharts"' in html
    assert "sm:grid-cols-2 xl:grid-cols-3" in html
    assert "max-w-[260px]" in html  # 帯・ゲージは最大幅固定＝件数が少なくても伸び切らない
    # 講師別の平均: 列見出しの並べ替え・注意ハイライト・全体平均（比較基準）の行
    assert "sortTutorTable" in html
    assert "scoreCellClass" in html
    assert "rateCellClass" in html
    assert "全体平均（比較基準）" in html
    # 全体平均との比較（差分バッジ・グレー点線のベンチマーク）
    assert "deltaChipHtml" in html
    assert "benchmarkSurveys" in html
    assert "border-dashed border-slate-500" in html
    # 絞り込みは1行＋クリアボタン
    assert 'id="filterClearBtn"' in html
# === 改修 202607231933 テスト END ===
