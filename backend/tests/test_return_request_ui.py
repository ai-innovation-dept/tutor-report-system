"""講師起点の差戻し要求（改修依頼 202607211144）の画面側の静的検証。

対応表（どのステータスで誰がボールを持つか）はサーバ（workflow_service.RETURN_REQUEST_BALL_HOLDERS）と
画面（講師の承認管理・参照画面・運営ダッシュボード）に複製されるため、ズレると
「要求ボタンは出るのに誰も対応できない」等の不整合になる。ここで同期を機械的に検証する。
"""
import re
from pathlib import Path

import app as app_package
from app.services.workflow_service import RETURN_REQUEST_BALL_HOLDERS
from tests.conftest import token

TEMPLATES = Path(app_package.__file__).resolve().parent / "templates"


def _template(*parts: str) -> str:
    return (TEMPLATES.joinpath(*parts)).read_text(encoding="utf-8")


def _js_array(source: str, name: str) -> list[str]:
    """`const NAME = ['a', 'b'];` 形式のJS配列を取り出す。"""
    match = re.search(rf"{name}\s*=\s*\[(.*?)\]", source, re.S)
    assert match, f"{name} が見つかりません"
    return re.findall(r"'([^']+)'", match.group(1))


def _js_map_entry(source: str, name: str, key: str) -> list[str]:
    """`const NAME = {{ key: ['a'], ... }}` から指定キーの配列を取り出す。"""
    block = re.search(rf"{name}\s*=\s*\{{(.*?)\}};", source, re.S)
    assert block, f"{name} が見つかりません"
    entry = re.search(rf"{key}\s*:\s*\[(.*?)\]", block.group(1), re.S)
    assert entry, f"{name}.{key} が見つかりません"
    return re.findall(r"'([^']+)'", entry.group(1))


# --- 講師の承認管理（要求する側） ---

def test_tutor_requestable_statuses_match_server(client):
    source = _template("tutor", "approval.html")
    assert sorted(_js_array(source, "REQUESTABLE_STATUSES")) == sorted(RETURN_REQUEST_BALL_HOLDERS)


def test_tutor_approval_has_request_ui(client):
    token(client, "tutor@example.com")
    res = client.get("/tutor/approval")
    assert res.status_code == 200
    # 要求ボタン・要求中の案内・却下通知・カードのバッジ
    assert "差戻しを要求" in res.text
    assert "requestReturnGroup" in res.text
    assert "差戻しを要求中です" in res.text
    assert "差戻し要求は許可されませんでした" in res.text
    assert "差戻し要求中" in res.text
    # 理由必須の入力モーダル（base.html の共通実装）を使う
    assert "showPromptModal" in res.text
    # 一括API（担当×対象月）を呼ぶ
    assert "/api/reports/request-return-bulk" in res.text


def test_tutor_approval_supports_past_months(client):
    """過去月も要求できるよう、承認管理は全月取得＋対象月の切替に対応している（202607211144 ③A）。"""
    token(client, "tutor@example.com")
    res = client.get("/tutor/approval")
    assert "api('/api/reports')" in res.text          # 当月固定の取得をやめている
    assert "selectedApprovalMonth" in res.text
    assert "group.target_month === selectedApprovalMonth" in res.text
    # 保護者は当月しか操作できないため、過去月の保護者承認待ちは要求対象外
    assert "pastMonth && r.status === 'awaiting_parent_approval'" in res.text


# --- 参照画面（対応する側） ---

def test_report_view_ball_table_matches_server(client):
    source = _template("report_view.html")
    for role in ("parent", "admin_receiver", "admin_reviewer"):
        expected = sorted(status for status, holder in RETURN_REQUEST_BALL_HOLDERS.items() if holder == role)
        assert sorted(_js_map_entry(source, "REQUEST_BALL_STATUSES", role)) == expected


def test_report_view_has_request_panel(client):
    source = _template("report_view.html")
    assert "講師から差戻し要求が届いています" in source          # 対応できるロール向けパネル
    assert "現在の承認担当の対応待ち" in source                  # 対応できない場合の案内
    assert "/api/reports/approve-return-request-bulk" in source
    assert "/api/reports/decline-return-request-bulk" in source
    assert "要求を許可（講師へ差戻す）" in source
    assert "却下理由" in source


# --- 運営ダッシュボード（気づく導線） ---

def test_admin_dashboard_shows_request_tasks(client):
    source = _template("admin", "dashboard.html")
    for role in ("admin_receiver", "admin_reviewer"):
        expected = sorted(status for status, holder in RETURN_REQUEST_BALL_HOLDERS.items() if holder == role)
        assert sorted(_js_map_entry(source, "REQUEST_BALL_STATUSES", role)) == expected
    # 通常の承認待ちに加えて差戻し要求もタスク（KPI件数と同一判定）に含める
    assert "function isTaskGroup" in source
    assert "isRequestTask" in source
    assert "差戻し要求に対応" in source
    # 履歴の表示ラベル
    assert "request_return: '差戻し要求（講師）'" in source
    assert "approve_return_request: '要求許可（講師へ差戻し）'" in source


def test_parent_approval_shows_request_notice(client):
    source = _template("parent", "approval.html")
    assert "講師から差戻し要求が届いています" in source
    assert "return_request_pending" in source
