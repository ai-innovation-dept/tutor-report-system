"""契約（WorkAssignmentProfile）から報告書フォームの動的列定義を生成する。

列構成（左→右）:
  固定（先頭）: 日付 / 業務開始時間 / 業務終了時間 / 担当時限
    ※「回数」「曜日」はフロントが自動生成するためデータ列には含めない
  動的: メイン業務①〜③ → サブ業務①〜⑤（登録があるもののみ）。常に「業務名（分）」1列（数値入力）
        採点（専用欄・scoring_enabled=True のときのみ）。「採点（回）」1列。
        1セルに「回」「分」の2入力を併記（type='count_minutes'。回数＋分数固定）
  固定（末尾）: 休憩時間（分） / 往復交通費（円） / 内容
データキー: メイン=task_minutes_N（旧来と互換）、サブ=sub_minutes_N。
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
MAX_MAIN_TASKS = 3
MAX_SUB_TASKS = 5
SCORING_LABEL = "採点"  # 項目名の既定値
SCORING_UNIT = "回"  # 単位の既定値（分は常に固定）


def _task_columns(profile: WorkAssignmentProfile, prefix: str, key_format: str, max_count: int) -> list[dict]:
    columns: list[dict] = []
    for index in range(1, max_count + 1):
        name = getattr(profile, f"{prefix}task_name_{index}")
        if not (name and str(name).strip()):
            continue
        columns.append({
            "key": key_format.format(index=index), "label": f"{str(name).strip()}（分）",
            "type": "number", "summable": True,
            "task_id": getattr(profile, f"{prefix}task_id_{index}"),
            "contract_id": getattr(profile, f"{prefix}contract_id_{index}"),
        })
    return columns


def build_column_definition(profile: WorkAssignmentProfile) -> list[dict]:
    """契約のメイン業務→サブ業務（いずれも分のみ）と採点専用欄から報告書の列定義を生成する。"""
    columns: list[dict] = [dict(c) for c in _LEADING_COLUMNS]
    columns += _task_columns(profile, "", "task_minutes_{index}", MAX_MAIN_TASKS)
    columns += _task_columns(profile, "sub_", "sub_minutes_{index}", MAX_SUB_TASKS)
    if profile.scoring_enabled:
        # 項目列: 1列に「{単位}」「分」を併記（1セル2入力）。データは scoring_count / scoring_minutes。
        # 項目名・単位は任意入力。未設定の既存契約は既定値（採点／回）へフォールバックする。分は固定。
        label = (getattr(profile, "scoring_label", None) or SCORING_LABEL).strip() or SCORING_LABEL
        unit = (getattr(profile, "scoring_unit", None) or SCORING_UNIT).strip() or SCORING_UNIT
        columns.append({
            "key": "scoring", "label": f"{label}（{unit}）",
            "type": "count_minutes", "summable": True, "unit": unit,
            "count_key": "scoring_count", "minutes_key": "scoring_minutes",
            "minutes_label": f"{label}（分）",
            "task_id": profile.scoring_task_id, "contract_id": profile.scoring_contract_id,
        })
    columns += [dict(c) for c in _TRAILING_COLUMNS]
    return columns
