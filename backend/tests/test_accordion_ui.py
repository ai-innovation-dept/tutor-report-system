"""承認管理カードのアコーディオンUI（改修依頼 202607201858）の静的検証。

講師の承認管理（生徒カード）のネイティブ <details> アコーディオンに、
クリック可能であることを示す「右端のシェブロン＋ホバー時のシャドウ持ち上げ」を追加した。
文字（「開く」等）に頼らず視認性を高める。旧・新システムで同一のクラス設計。
"""
from tests.conftest import token


def test_tutor_approval_accordion_affordance(client):
    token(client, "tutor@example.com")  # ログイン（cookieセット）
    res = client.get("/tutor/approval")
    assert res.status_code == 200
    # ネイティブ<details>カードにホバー（シャドウ持ち上げ）と開時のシェブロン反転CSS
    assert ".accordion-card:hover" in res.text
    assert "accordion-card[open] .accordion-chevron" in res.text
    # summary に下向きシェブロンSVGを配置（開時はCSSで180°反転して上向き）
    assert "accordion-summary flex cursor-pointer list-none" in res.text
    assert 'class="accordion-chevron' in res.text
    assert "${ACCORDION_CHEVRON}" in res.text
