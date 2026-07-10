"""
ワークフロー定義の唯一の情報源。
ステータス比較はこのファイル以外で行わないこと。
遷移を追加するには TRANSITIONS にエントリを1行追加するだけでよい。
"""
from dataclasses import dataclass, field


class WorkStatus:
    DRAFT = "draft"
    # 事務の事前確認待ち（担当業務の月分が契約の月分固定を超過した報告のみ。学校確認の前段）
    AWAITING_OFFICE_PRECHECK = "awaiting_office_precheck"
    AWAITING_SCHOOL = "awaiting_school"
    AWAITING_SALES = "awaiting_sales"
    AWAITING_OFFICE = "awaiting_office"
    AWAITING_FINANCE = "awaiting_finance"
    APPROVED = "approved"
    RETURNED_TO_TUTOR = "returned_to_tutor"
    RETURNED_TO_OFFICE = "returned_to_office"
    CLOSED = "closed"

    ALL = {
        DRAFT, AWAITING_OFFICE_PRECHECK, AWAITING_SCHOOL, AWAITING_SALES, AWAITING_OFFICE,
        AWAITING_FINANCE, APPROVED, RETURNED_TO_TUTOR, RETURNED_TO_OFFICE, CLOSED,
    }


class WorkAction:
    SUBMIT = "submit"
    APPROVE = "approve"
    RETURN = "return"
    SKIP_SCHOOL = "skip_school"
    CLOSE = "close"
    # 講師起点の差戻し要求（ステータスは変えない）。ボールを持つロールが許可すると講師へ差戻る。
    REQUEST_RETURN = "request_return"
    APPROVE_RETURN_REQUEST = "approve_return_request"
    DECLINE_RETURN_REQUEST = "decline_return_request"


@dataclass(frozen=True)
class Transition:
    from_status: str
    action: str
    allowed_roles: frozenset[str]
    to_status: str
    comment_required: bool = False
    # Noneは遷移後のcurrent_approver_roleを自動解決させる
    next_approver_role: str | None = None


# 遷移表：この1つのリストがワークフロー全体を定義する
TRANSITIONS: list[Transition] = [
    # 通常フロー
    Transition(
        from_status=WorkStatus.DRAFT,
        action=WorkAction.SUBMIT,
        allowed_roles=frozenset({"tutor"}),
        to_status=WorkStatus.AWAITING_SCHOOL,
        next_approver_role="school",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_SCHOOL,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"school"}),
        to_status=WorkStatus.AWAITING_OFFICE,
        next_approver_role="office",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_OFFICE,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.AWAITING_SALES,
        next_approver_role="sales",
    ),
    # 営業が最終承認（経理ステップを廃止し、営業承認で完了とする）
    Transition(
        from_status=WorkStatus.AWAITING_SALES,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"sales"}),
        to_status=WorkStatus.APPROVED,
        next_approver_role=None,
    ),
    # スキップ（学校承認不要）
    Transition(
        from_status=WorkStatus.DRAFT,
        action=WorkAction.SKIP_SCHOOL,
        allowed_roles=frozenset({"admin_chief"}),
        to_status=WorkStatus.AWAITING_OFFICE,
        next_approver_role="office",
    ),
    # 超過フロー（講師→事務の事前確認→学校→事務→営業）。
    # 提出時の超過判定で engine が awaiting_school → awaiting_office_precheck に差し替える。
    # 事前確認の承認で通常フロー（学校確認待ち）へ合流する。
    Transition(
        from_status=WorkStatus.AWAITING_OFFICE_PRECHECK,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.AWAITING_SCHOOL,
        next_approver_role="school",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_OFFICE_PRECHECK,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.RETURNED_TO_TUTOR,
        comment_required=True,
        next_approver_role="tutor",
    ),
    # 差戻し
    Transition(
        from_status=WorkStatus.AWAITING_SCHOOL,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"school"}),
        to_status=WorkStatus.RETURNED_TO_TUTOR,
        comment_required=True,
        next_approver_role="tutor",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_OFFICE,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.RETURNED_TO_TUTOR,
        comment_required=True,
        next_approver_role="tutor",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_SALES,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"sales"}),
        to_status=WorkStatus.RETURNED_TO_OFFICE,
        comment_required=True,
        next_approver_role="office",
    ),
    # 最終承認済み（完了）からの差戻し（営業が完了後に修正を依頼する）
    Transition(
        from_status=WorkStatus.APPROVED,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"sales"}),
        to_status=WorkStatus.RETURNED_TO_OFFICE,
        comment_required=True,
        next_approver_role="office",
    ),
    # 再提出
    Transition(
        from_status=WorkStatus.RETURNED_TO_TUTOR,
        action=WorkAction.SUBMIT,
        allowed_roles=frozenset({"tutor"}),
        to_status=WorkStatus.AWAITING_SCHOOL,
        next_approver_role="school",
    ),
    Transition(
        from_status=WorkStatus.RETURNED_TO_OFFICE,
        action=WorkAction.SUBMIT,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.AWAITING_SALES,
        next_approver_role="sales",
    ),
    # 事務へ差し戻された報告を事務が処理する（営業/経理からの差戻しは事務が受け持つ）
    # 承認＝前進（営業確認待ちへ）
    Transition(
        from_status=WorkStatus.RETURNED_TO_OFFICE,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.AWAITING_SALES,
        next_approver_role="sales",
    ),
    # 事務がさらに講師へ差し戻す
    Transition(
        from_status=WorkStatus.RETURNED_TO_OFFICE,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.RETURNED_TO_TUTOR,
        comment_required=True,
        next_approver_role="tutor",
    ),
]

# 講師起点の差戻し要求（request_return）と、ボールを持つロールによる許可・却下。
# - 要求・却下はステータスを変えない（イベント記録のみ）。許可で講師へ差戻す。
# - 要求は承認等でボールが移っても未解決のまま引き継がれ、その時点のボール保持ロールが対応する
#   （未解決かどうかは WorkReport.return_request_pending がイベント履歴から導出する）。
# 値: 対象ステータス → (ボールを持つロール, そのステータスの current_approver_role)
RETURN_REQUEST_BALL_HOLDERS: dict[str, tuple[str, str | None]] = {
    WorkStatus.AWAITING_OFFICE_PRECHECK: ("office", "office"),
    WorkStatus.AWAITING_SCHOOL: ("school", "school"),
    WorkStatus.AWAITING_OFFICE: ("office", "office"),
    WorkStatus.AWAITING_SALES: ("sales", "sales"),
    WorkStatus.APPROVED: ("sales", None),
    WorkStatus.RETURNED_TO_OFFICE: ("office", "office"),
}

for _status, (_holder_role, _approver_role) in RETURN_REQUEST_BALL_HOLDERS.items():
    TRANSITIONS.append(
        Transition(
            from_status=_status,
            action=WorkAction.REQUEST_RETURN,
            allowed_roles=frozenset({"tutor"}),
            to_status=_status,
            comment_required=True,
            next_approver_role=_approver_role,
        )
    )
    TRANSITIONS.append(
        Transition(
            from_status=_status,
            action=WorkAction.APPROVE_RETURN_REQUEST,
            allowed_roles=frozenset({_holder_role}),
            to_status=WorkStatus.RETURNED_TO_TUTOR,
            next_approver_role="tutor",
        )
    )
    TRANSITIONS.append(
        Transition(
            from_status=_status,
            action=WorkAction.DECLINE_RETURN_REQUEST,
            allowed_roles=frozenset({_holder_role}),
            to_status=_status,
            comment_required=True,
            next_approver_role=_approver_role,
        )
    )

# (from_status, action) → Transition の高速ルックアップ用インデックス
# 同一 (from_status, action) に複数ロールがある場合は別エントリになる
_INDEX: dict[tuple[str, str], list[Transition]] = {}
for _t in TRANSITIONS:
    _key = (_t.from_status, _t.action)
    _INDEX.setdefault(_key, []).append(_t)


def find_transition(from_status: str, action: str, actor_role: str) -> Transition | None:
    """ロールを含む条件で該当する遷移を返す。なければ None。"""
    for t in _INDEX.get((from_status, action), []):
        if actor_role in t.allowed_roles:
            return t
    return None
