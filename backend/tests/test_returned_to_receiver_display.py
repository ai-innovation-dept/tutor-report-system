"""講師の承認管理で `returned_to_receiver`（再鑑者→受付への運営内差戻し）を正しく扱うことの静的検証。

改修 202607231643 のバグ修正。
`tutor/approval.html` の monthlyPhase の rank マップに returned_to_receiver が欠落しており
（`?? 0` で draft と同じ rank 0 に落ちる）、この状態を含む月が phase='recording' と誤判定され、
「まだ記録中の指導日があります」「保護者へ依頼日時 作成中」と誤表示されていた（かつ報告書一覧では
returned_to_receiver は進行中扱いで「対象月の報告書がすでに進行中です」となり、講師は編集も作成もできず
手詰まりに見えた）。恒久修正として monthlyPhase / PHASE_CURRENT_STEP / STATUS_LABELS / actionArea の
4箇所で returned_to_receiver を専用フェーズ（運営内で差戻し中）として扱う。

monthlyPhase は client-side JS のため、配信 HTML に修正が織り込まれていることを静的に検証する
（既存の approval.html 静的検証テスト test_accordion_ui.py と同じ方式）。
"""
from tests.conftest import token


def test_tutor_approval_handles_returned_to_receiver(client):
    token(client, "tutor@example.com")  # ログイン（cookieセット）
    res = client.get("/tutor/approval")
    assert res.status_code == 200
    body = res.text

    # ① monthlyPhase の rank マップに returned_to_receiver がある（欠落＝rank0=recording 誤判定の根本原因）
    assert "returned_to_receiver: 3," in body
    # ② returned_to_receiver を含む月は専用フェーズへ（recording に落とさない）
    assert "if (statuses.includes('returned_to_receiver')) return 'returned_to_receiver';" in body
    # ③ 差戻し(returned_to_tutor)の判定が returned_to_receiver 判定より前にある（講師対応が優先）
    assert body.index("statuses.includes('returned_to_tutor')") < body.index(
        "statuses.includes('returned_to_receiver')"
    )
    # ④ ステッパーの現在ステップ（運営承認＝index2）に割り当て
    assert "returned_to_receiver: 2," in body
    # ⑤ 状態ラベル（詳細テーブルで raw 文字列が出ないように）
    assert "returned_to_receiver: '運営内差戻し'," in body
    # ⑥ actionArea に専用の案内パネル（講師は待ち・必要なら差戻し要求）
    assert "group.phase === 'returned_to_receiver'" in body
    assert "運営内で差戻し中です" in body


def test_tutor_approval_does_not_mislabel_returned_to_receiver_as_recording(client):
    """回帰防止: returned_to_receiver が recording フェーズの分岐（まだ記録中）へ落ちないこと。

    monthlyPhase 末尾の `return 'recording'` より前に returned_to_receiver の早期 return が置かれている
    ことを配信 HTML の並び順で確認する。
    """
    token(client, "tutor@example.com")
    body = client.get("/tutor/approval").text
    early_return = body.index("statuses.includes('returned_to_receiver')")
    recording_fallthrough = body.index("return 'recording';")
    assert early_return < recording_fallthrough
