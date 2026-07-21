"""講師起点の差戻し要求（改修依頼 202607211144）の統合テスト。

講師は「差戻しを要求」（理由必須）するだけで、実行するのはその時点でボールを持つ承認担当:
  保護者承認待ち→保護者 / 受付待ち・受付へ差戻し中→受付 / 受付済み・再鑑済み・最終承認済み→再鑑。
許可(approve)で講師へ差戻し、却下(decline・理由必須)は要求のみ解消する（ステータスは変わらない）。
未解決かどうかは report_events から導出するため、承認でボールが移っても要求は引き継がれる。

メールは実送信しない（conftest で MAIL_BACKEND=console。要求・却下はそもそも送信処理を通らない）。
"""
from app.core.time import get_current_jst_date
from app.models import Assignment, ReportStatus, User
from tests.conftest import seed_monthly_report, token


def _headers(client, email):
    return {"Authorization": f"Bearer {token(client, email)}"}


def _create_report(client, db, *, lesson_date=None):
    assignment = db.query(Assignment).first()
    seed_monthly_report(db, assignment)
    res = client.post("/api/reports", headers=_headers(client, "tutor@example.com"), json={
        "assignment_id": str(assignment.id),
        "lesson_date": str(lesson_date or get_current_jst_date()),
        "start_time": "18:00",
        "end_time": "19:00",
        "subject": "算数",
        "content": "指導内容",
    })
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _advance(client, report_id, steps):
    """(メール, エンドポイント) の順に承認を進める。"""
    for email, endpoint in steps:
        res = client.post(f"/api/reports/{report_id}/{endpoint}", headers=_headers(client, email), json={})
        assert res.status_code == 200, res.text
    return res.json()


def _report(client, report_id, email="master@example.com"):
    listed = client.get("/api/reports", headers=_headers(client, email))
    assert listed.status_code == 200, listed.text
    return next(item for item in listed.json() if item["id"] == report_id)


def _request_return(client, report_id, comment="入力誤りを修正したい", email="tutor@example.com"):
    return client.post(
        "/api/reports/request-return-bulk",
        headers=_headers(client, email),
        json={"report_ids": [report_id], "comment": comment},
    )


def _approve_request(client, report_id, email):
    return client.post(
        "/api/reports/approve-return-request-bulk",
        headers=_headers(client, email),
        json={"report_ids": [report_id]},
    )


def _decline_request(client, report_id, email, comment="今月は締めのため対応できません"):
    return client.post(
        "/api/reports/decline-return-request-bulk",
        headers=_headers(client, email),
        json={"report_ids": [report_id], "comment": comment},
    )


# --- 要求（講師） ---

def test_request_return_records_pending_without_status_change(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent")])

    res = _request_return(client, rid)
    assert res.status_code == 200, res.text
    report = _report(client, rid)
    # ステータスは変わらず、未解決の要求として講師にも承認担当にも見える
    assert report["status"] == ReportStatus.awaiting_parent_approval.value
    assert report["return_request_pending"] is True
    assert report["return_request_comment"] == "入力誤りを修正したい"
    assert report["return_request_declined_comment"] is None
    assert report["events"][-1]["action"] == "request_return"


def test_request_return_requires_comment(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent")])
    res = client.post(
        "/api/reports/request-return-bulk",
        headers=_headers(client, "tutor@example.com"),
        json={"report_ids": [rid], "comment": "   "},
    )
    assert res.status_code == 422  # スキーマ側で空文字を弾く


def test_request_return_rejected_while_pending(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent")])
    assert _request_return(client, rid).status_code == 200
    res = _request_return(client, rid, comment="二重要求")
    assert res.status_code == 409
    assert "受付済み" in res.json()["detail"]


def test_request_return_not_allowed_while_tutor_holds_report(client, db):
    """下書き・差戻し中は講師自身が編集できるため要求できない。"""
    rid = _create_report(client, db)
    res = _request_return(client, rid)
    assert res.status_code == 409


def test_request_return_forbidden_for_other_roles(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent")])
    # 保護者は自分の担当報告書でも「要求」はできない（要求は講師のみ）
    res = _request_return(client, rid, email="parent@example.com")
    assert res.status_code in (403, 409)


# --- 許可（ボール保持ロール） ---

def test_parent_can_approve_request_and_report_returns_to_tutor(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent")])
    assert _request_return(client, rid).status_code == 200

    res = _approve_request(client, rid, "parent@example.com")
    assert res.status_code == 200, res.text
    report = _report(client, rid)
    assert report["status"] == ReportStatus.returned_to_tutor.value
    assert report["return_request_pending"] is False
    # 要求理由は許可イベントのコメントへ自動転記され、講師画面の差戻し理由として読める
    assert "【講師の差戻し要求】入力誤りを修正したい" in report["last_return_comment"]
    assert report["events"][-1]["action"] == "approve_return_request"


def test_receiver_can_approve_request(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200
    assert _approve_request(client, rid, "receiver@example.com").status_code == 200
    assert _report(client, rid)["status"] == ReportStatus.returned_to_tutor.value


def test_reviewer_can_approve_request_after_final_approval(client, db):
    """最終承認済み（完了）でも再鑑担当が許可すると講師へ直接差戻る（202607211144 ②A）。"""
    rid = _create_report(client, db)
    _advance(client, rid, [
        ("tutor@example.com", "submit-to-parent"),
        ("parent@example.com", "parent-approve"),
        ("receiver@example.com", "receive"),
        ("reviewer@example.com", "re-review"),
    ])
    assert _report(client, rid)["status"] == ReportStatus.admin_approved.value

    assert _request_return(client, rid, comment="指導時間の訂正").status_code == 200
    assert _approve_request(client, rid, "reviewer@example.com").status_code == 200
    report = _report(client, rid)
    assert report["status"] == ReportStatus.returned_to_tutor.value
    assert "指導時間の訂正" in report["last_return_comment"]


def test_approve_requires_ball_holder_role(client, db):
    """ボールを持たないロール（受付待ちの段階での再鑑担当など）は許可できない。"""
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200
    res = _approve_request(client, rid, "reviewer@example.com")  # 受付待ち＝ボールは受付
    assert res.status_code == 403
    assert _report(client, rid)["status"] == ReportStatus.submitted_to_admin.value


def test_approve_without_pending_request_is_conflict(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    res = _approve_request(client, rid, "receiver@example.com")
    assert res.status_code == 409
    assert "未対応の差戻し要求がありません" in res.json()["detail"]


# --- 却下（ボール保持ロール） ---

def test_decline_keeps_status_and_shows_reason_to_tutor(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200

    res = _decline_request(client, rid, "receiver@example.com")
    assert res.status_code == 200, res.text
    report = _report(client, rid)
    assert report["status"] == ReportStatus.submitted_to_admin.value  # ステータスは変わらない
    assert report["return_request_pending"] is False
    assert report["return_request_declined_comment"] == "今月は締めのため対応できません"


def test_tutor_can_request_again_after_decline(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200
    assert _decline_request(client, rid, "receiver@example.com").status_code == 200

    assert _request_return(client, rid, comment="再要求").status_code == 200
    report = _report(client, rid)
    assert report["return_request_pending"] is True
    assert report["return_request_comment"] == "再要求"
    assert report["return_request_declined_comment"] is None


def test_decline_requires_comment(client, db):
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200
    res = client.post(
        "/api/reports/decline-return-request-bulk",
        headers=_headers(client, "receiver@example.com"),
        json={"report_ids": [rid], "comment": ""},
    )
    assert res.status_code == 422


# --- ボールの引継ぎ・解決 ---

def test_pending_request_carries_over_to_next_ball_holder(client, db):
    """要求は承認でボールが移っても未解決のまま引き継がれ、新しい担当が対応する。"""
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200

    # 受付が要求に触れずに受付承認 → ボールは再鑑へ移るが要求は残る
    assert client.post("/api/reports/{}/receive".format(rid), headers=_headers(client, "receiver@example.com"), json={}).status_code == 200
    report = _report(client, rid)
    assert report["status"] == ReportStatus.received.value
    assert report["return_request_pending"] is True
    # 受付はもうボールを持たないため対応できない／再鑑は対応できる
    assert _approve_request(client, rid, "receiver@example.com").status_code == 403
    assert _approve_request(client, rid, "reviewer@example.com").status_code == 200
    assert _report(client, rid)["status"] == ReportStatus.returned_to_tutor.value


def test_return_by_approver_resolves_pending_request(client, db):
    """承認担当が通常の差戻しを行った場合も要求は解決済みになる（講師の手元に戻るため）。"""
    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    assert _request_return(client, rid).status_code == 200

    res = client.post(
        "/api/reports/admin-return-bulk",
        headers=_headers(client, "receiver@example.com"),
        json={"report_ids": [rid], "from_role": "receiver", "comment": "記載内容の確認をお願いします"},
    )
    assert res.status_code == 200, res.text
    report = _report(client, rid)
    assert report["status"] == ReportStatus.returned_to_tutor.value
    assert report["return_request_pending"] is False


# --- 職務分掌 ---

def test_separation_of_duties_blocks_request_handling(client, db):
    """同一報告書で受付を担当した人は、再鑑側の要求対応（許可）もできない。"""
    # 受付担当に再鑑ロールも持たせ、同一報告書で受付→再鑑の兼務を試みる
    receiver = db.query(User).filter(User.email == "receiver@example.com").one()
    receiver.roles = ["admin_receiver", "admin_reviewer"]
    db.commit()

    rid = _create_report(client, db)
    _advance(client, rid, [
        ("tutor@example.com", "submit-to-parent"),
        ("parent@example.com", "parent-approve"),
        ("receiver@example.com", "receive"),
    ])
    assert _request_return(client, rid).status_code == 200

    res = _approve_request(client, rid, "receiver@example.com")
    assert res.status_code == 409
    assert "兼務" in res.json()["detail"]
    # 兼務していない再鑑担当なら対応できる
    assert _approve_request(client, rid, "reviewer@example.com").status_code == 200


# --- 通知（実メールを飛ばさないことの確認を含む） ---

def test_request_and_decline_do_not_notify(client, db):
    """要求・却下はメール送信処理を通らない（到達は画面のバッジ／タスク表示）。"""
    from app.models import Notification

    rid = _create_report(client, db)
    _advance(client, rid, [("tutor@example.com", "submit-to-parent"), ("parent@example.com", "parent-approve")])
    before = db.query(Notification).count()

    assert _request_return(client, rid).status_code == 200
    assert _decline_request(client, rid, "receiver@example.com").status_code == 200
    db.expire_all()
    assert db.query(Notification).count() == before  # 状態変更通知も増えない
