"""契約（WorkAssignmentProfile）から報告書フォームの動的列定義を生成する。

列構成（左→右）:
  固定（先頭）: 日付 / 業務開始時間 / 業務終了時間 / 担当時限
    ※「回数」「曜日」はフロントが自動生成するためデータ列には含めない
  動的: 委託業務①〜⑤の「業務名（分）」（登録があるもののみ）
        採点（回）/ 採点（分）（has_scoring=True のときのみ）
  固定（末尾）: 休憩時間（分） / 往復交通費（円） / 内容
"""
from app.models.work import WorkAssignmentProfile

_LEADING_COLUMNS = (
    {"key": "date", "label": "日付", "type": "date", "summable": False},
    {"key": "start", "label": "業務開始時間", "type": "time", "summable": False},
    {"key": "end", "label": "業務終了時間", "type": "time", "summable": False},
    {"key": "subject_period", "label": "担当時限", "type": "number", "summable": False},
)
_TRAILING_COLUMNS = (
    {"key": "break_minutes", "label": "休憩時間（分）", "type": "number", "summable": True},
    {"key": "commute_fee", "label": "往復交通費（円）", "type": "number", "summable": True},
    {"key": "note", "label": "内容", "type": "text", "summable": False},
)
MAX_TASKS = 5


def build_column_definition(profile: WorkAssignmentProfile) -> list[dict]:
    """契約の委託業務・採点設定から報告書の列定義（list[dict]）を生成する。"""
    columns: list[dict] = [dict(c) for c in _LEADING_COLUMNS]
    for index in range(1, MAX_TASKS + 1):
        name = getattr(profile, f"task_name_{index}")
        if name and str(name).strip():
            columns.append({
                "key": f"task_minutes_{index}",
                "label": f"{name}（分）",
                "type": "number",
                "summable": True,
                "task_id": getattr(profile, f"task_id_{index}"),
                "contract_id": getattr(profile, f"contract_id_{index}"),
            })
    if profile.has_scoring:
        columns.append({"key": "scoring_count", "label": "採点（回）", "type": "number", "summable": True})
        columns.append({"key": "scoring_minutes", "label": "採点（分）", "type": "number", "summable": True})
    columns += [dict(c) for c in _TRAILING_COLUMNS]
    return columns
