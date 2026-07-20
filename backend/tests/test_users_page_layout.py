"""受付ロール以上のユーザー管理画面のテーブルを EMPS と同構成へ最適化（改修依頼 202607202109）の静的検証。

EMPS（new_backend・202607171705）と同じ構成へ:
- ロール列＝バッジ表示（チェックボックス方式 min-w-72 は撤去）
- 行内「更新」ボタンは廃止し、ロール変更は「詳細」ドロワーへ集約
- 状態・招待状態はバッジ表示
- 操作ボタンは row-actions でコンパクト（スマホのみ 44px）
- 新規ユーザー登録フォームは「招待メールを送る」を同一行へ（改修依頼 202607201825）
"""
from tests.conftest import token


def test_users_table_matches_emps_layout(client):
    token(client, "master@example.com")  # admin_master は /admin/users 可（cookieセット）
    res = client.get("/admin/users")
    assert res.status_code == 200
    body = res.text
    # 招待フォーム1行化（202607201825）: ロール・氏名・メール・送信ボタンを1グリッド行に
    assert "md:grid-cols-[150px_minmax(0,1fr)_minmax(0,1fr)_auto]" in body
    assert "h-[42px]" in body
    # ロール列＝バッジ／招待状態＝バッジ
    assert "function roleBadges(roles)" in body
    assert "${roleBadges(userRoles(user))}" in body
    assert "function invitationStatusBadge(invitation)" in body
    # 操作は row-actions（PCコンパクト・スマホのみ44px）。旧チェックボックス方式(min-w-72)は撤去
    assert "row-actions flex flex-nowrap gap-1.5" in body
    assert ".mobile-cards tbody td .row-actions button { min-height: 44px" in body
    assert "min-w-72" not in body
    # ロール変更は詳細ドロワーへ集約（行内更新ボタン廃止の受け皿を維持）
    assert "roleCheckboxes(user, 'drawer')" in body
    assert "{keepDrawer: true}" in body
