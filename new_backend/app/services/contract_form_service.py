"""契約（WorkAssignmentProfile）から報告書フォームの動的列定義を生成する。

列構成（左→右）:
  固定（先頭）: 日付 / 業務開始時間 / 業務終了時間 / 担当時限
    ※「回数」「曜日」はフロントが自動生成するためデータ列には含めない
  動的: 委託業務①〜⑤（登録があるもののみ）。各業務の入力形式により
        - 'minutes'       … 「業務名（分）」1列
        - 'count_minutes' … 「業務名（回）」＋「業務名（分）」2列
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
    """契約の委託業務・入力形式から報告書の列定義（list[dict]）を生成する。"""
    columns: list[dict] = [dict(c) for c in _LEADING_COLUMNS]
    for index in range(1, MAX_TASKS + 1):
        name = getattr(profile, f"task_name_{index}")
        if not (name and str(name).strip()):
            continue
        label = str(name).strip()
        task_id = getattr(profile, f"task_id_{index}")
        contract_id = getattr(profile, f"contract_id_{index}")
        task_format = getattr(profile, f"task_format_{index}", None) or "minutes"
        meta = {"task_id": task_id, "contract_id": contract_id}
        if task_format == "count_minutes":
            columns.append({
                "key": f"task_count_{index}", "label": f"{label}（回）",
                "type": "number", "summable": True, **meta,
            })
        columns.append({
            "key": f"task_minutes_{index}", "label": f"{label}（分）",
            "type": "number", "summable": True, **meta,
        })
    columns += [dict(c) for c in _TRAILING_COLUMNS]
    return columns
