"""
ワークフロー定義の唯一の情報源。
ステータス比較はこのファイル以外で行わないこと。
遷移を追加するには TRANSITIONS にエントリを1行追加するだけでよい。
"""
from dataclasses import dataclass, field


class WorkStatus:
    DRAFT = "draft"
    AWAITING_SCHOOL = "awaiting_school"
    AWAITING_SALES = "awaiting_sales"
    AWAITING_OFFICE = "awaiting_office"
    AWAITING_FINANCE = "awaiting_finance"
    APPROVED = "approved"
    RETURNED_TO_TUTOR = "returned_to_tutor"
    RETURNED_TO_SALES = "returned_to_sales"
    CLOSED = "closed"

    ALL = {
        DRAFT, AWAITING_SCHOOL, AWAITING_SALES, AWAITING_OFFICE,
        AWAITING_FINANCE, APPROVED, RETURNED_TO_TUTOR, RETURNED_TO_SALES, CLOSED,
    }


class WorkAction:
    SUBMIT = "submit"
    APPROVE = "approve"
    RETURN = "return"
    SKIP_SCHOOL = "skip_school"
    CLOSE = "close"


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
        to_status=WorkStatus.AWAITING_SALES,
        next_approver_role="sales",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_SALES,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"sales"}),
        to_status=WorkStatus.AWAITING_OFFICE,
        next_approver_role="office",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_OFFICE,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.AWAITING_FINANCE,
        next_approver_role="admin_master",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_FINANCE,
        action=WorkAction.APPROVE,
        allowed_roles=frozenset({"admin_master"}),
        to_status=WorkStatus.APPROVED,
        next_approver_role=None,
    ),
    # スキップ（学校承認不要）
    Transition(
        from_status=WorkStatus.DRAFT,
        action=WorkAction.SKIP_SCHOOL,
        allowed_roles=frozenset({"sales", "office", "admin_master"}),
        to_status=WorkStatus.AWAITING_SALES,
        next_approver_role="sales",
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
        from_status=WorkStatus.AWAITING_SALES,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"sales"}),
        to_status=WorkStatus.RETURNED_TO_TUTOR,
        comment_required=True,
        next_approver_role="tutor",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_OFFICE,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"office"}),
        to_status=WorkStatus.RETURNED_TO_SALES,
        comment_required=True,
        next_approver_role="sales",
    ),
    Transition(
        from_status=WorkStatus.AWAITING_FINANCE,
        action=WorkAction.RETURN,
        allowed_roles=frozenset({"admin_master"}),
        to_status=WorkStatus.RETURNED_TO_SALES,
        comment_required=True,
        next_approver_role="sales",
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
        from_status=WorkStatus.RETURNED_TO_SALES,
        action=WorkAction.SUBMIT,
        allowed_roles=frozenset({"sales"}),
        to_status=WorkStatus.AWAITING_OFFICE,
        next_approver_role="office",
    ),
]

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
