"""改修 2026072120937 ②: 契約期間外の指導日を入力したときの確認ポップアップ。

要件（利用者確認済み）:
  - Q1: 講師フォームの各行の「日付（指導日）」が契約期間（contract_start〜contract_end）の外のとき、
        「契約期間外ですが問題ないですか？」を表示する。
  - Q2=b: 「いいえ」を押しても日付は取り消さない（警告のみ）。将来 Q2=a（取り消し）へ切り替える
          場合は CLEAR_DATE_WHEN_OUTSIDE_CONTRACT を true にするだけ（呼び出し側 revert が動く）。

判定は全体の契約期間（前期/後期の適用期間ではない）。契約期間が未設定の契約は判定しない。
明細行（PC）とスマホの詳細シートの両方に同じ確認を配線する。

実装はフロント（tutor/reports.html のクライアント JS）のため、ここでは「配線が意図どおり
出力されている」ことをページHTML文字列で検証する（DB/API変更なし・実メール送信ゼロ）。
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.main import app
from app.models.shared import User
from tests.conftest import TestSession


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def tutor():
    s = TestSession()
    try:
        user = User(email="cp-tutor@example.com", role="tutor", roles=["tutor"],
                    display_name="契約期間テスト講師", allowed_systems=["new"],
                    password_hash=hash_password("Passw0rd!"))
        s.add(user)
        s.commit()
    finally:
        s.close()


def _login(client):
    res = client.post("/api/auth/login", json={"username": "cp-tutor@example.com", "password": "Passw0rd!"})
    assert res.status_code == 200, res.text


class TestContractPeriodDateWarningWiring:
    def test_warning_helper_and_message_present(self, client, tutor):
        _login(client)
        html = client.get("/tutor/reports").text
        # そもそも講師フォームが取得できている
        assert 'id="lineSheetOverlay"' in html
        # 指定どおりの確認メッセージ
        assert "契約期間外ですが問題ないですか？" in html
        # 共通ヘルパーが定義されている
        assert "function confirmLineDateWithinContract" in html
        # 判定基準＝全体の契約期間（contract_start〜contract_end）・ISO文字列比較
        assert "(start && dateValue < start) || (end && dateValue > end)" in html

    def test_wired_into_both_desktop_and_mobile(self, client, tutor):
        _login(client)
        html = client.get("/tutor/reports").text
        # 明細行（PC）の日付 change で呼ばれる
        assert "confirmLineDateWithinContract(input.value" in html
        # スマホの詳細シートの日付 change で呼ばれる
        assert "confirmLineDateWithinContract(dateInput.value" in html

    def test_cancel_keeps_date_by_default_q2b(self, client, tutor):
        _login(client)
        html = client.get("/tutor/reports").text
        # Q2=b: 既定では「いいえ」でも日付を取り消さない（フラグ false）
        assert "const CLEAR_DATE_WHEN_OUTSIDE_CONTRACT = false" in html
        # 取り消し処理はフラグが true のときだけ実行される（将来 Q2=a への一点切替）
        assert "if (!ok && CLEAR_DATE_WHEN_OUTSIDE_CONTRACT" in html
