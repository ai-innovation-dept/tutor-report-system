"""契約（WorkAssignmentProfile）から報告書フォームの動的列定義を生成する。

列構成（左→右）:
  固定（先頭）: 日付 / 業務開始時間 / 業務終了時間 / 担当時限
    ※「回数」「曜日」はフロントが自動生成するためデータ列には含めない
  動的: 担当業務（前期/後期のうち入力タイミング＝今日基準で適用中の期の1列のみ）
        → サブ業務①〜⑤（登録があるもののみ）。常に「業務名（分）」1列（数値入力）
        採点（専用欄・scoring_enabled=True のときのみ）。「採点（回）」1列。
        1セルに「回」「分」の2入力を併記（type='count_minutes'。回数＋分数固定）
  固定（末尾）: 休憩時間（分） / 往復交通費（円） / 内容
データキー: 担当業務=task_minutes_N（N=1:前期 / 2:後期。旧来と互換）、サブ=sub_minutes_N。
"""
import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
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
MAX_MAIN_TASKS = 2  # 担当業務は前期(1)・後期(2)の2本
MAX_SUB_TASKS = 5
SCORING_LABEL = "採点"  # 項目名の既定値
SCORING_UNIT = "回"  # 単位の既定値（分は常に固定）


def month_bounds(target_month: str | None) -> tuple[date, date] | None:
    """"YYYY-MM" から月初日・月末日を返す。形式不正・未指定は None。"""
    try:
        year, month = (int(part) for part in str(target_month or "").split("-", 1))
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    except (ValueError, AttributeError):
        return None


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _today_jst() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


def active_term_case(profile: WorkAssignmentProfile, target_month: str | None, today: date | None = None) -> dict | None:
    """対象月の報告書に適用する期別ケース（前期/後期）を**1つだけ**返す（該当なしは None）。

    基準日＝入力タイミング（今日JST）。対象月外の月（過去月の差戻し編集・事務修正など）は
    今日を対象月内へクランプした日（＝過去月なら月末時点）で判定する。
    解決順: 基準日を含む期 → 対象月と重なる期（適用開始日順）。
    適用期間（開始・終了）を持つ新形式のケースのみが対象で、期間なしの旧ケースしか無い契約は
    None（列・コマ設定とも従来動作へフォールバック）。クライアント（work_report_calc.js の
    activeTermCaseForMonth）と同一ルール。
    """
    dated_cases: list[tuple[date, date, dict]] = []
    for case in profile.workload_cases or []:
        if not isinstance(case, dict):
            continue
        start, end = _parse_date(case.get("start_date")), _parse_date(case.get("end_date"))
        if start and end:
            dated_cases.append((start, end, case))
    if not dated_cases:
        return None
    dated_cases.sort(key=lambda item: (item[0], int(item[2].get("task_index") or 1)))
    today = today or _today_jst()
    bounds = month_bounds(target_month)
    ref = today if bounds is None else min(max(today, bounds[0]), bounds[1])
    for start, end, case in dated_cases:
        if start <= ref <= end:
            return case
    if bounds is not None:
        month_start, month_end = bounds
        for start, end, case in dated_cases:
            if start <= month_end and end >= month_start:
                return case
    return None


def _task_column(profile: WorkAssignmentProfile, prefix: str, key_format: str, index: int) -> dict | None:
    name = getattr(profile, f"{prefix}task_name_{index}")
    if not (name and str(name).strip()):
        return None
    return {
        "key": key_format.format(index=index), "label": f"{str(name).strip()}（分）",
        "type": "number", "summable": True,
        "task_id": getattr(profile, f"{prefix}task_id_{index}"),
        "contract_id": getattr(profile, f"{prefix}contract_id_{index}"),
    }


def _main_task_columns(profile: WorkAssignmentProfile, target_month: str | None) -> list[dict]:
    """担当業務（前期/後期）のうち、対象月の報告書に適用する期の**1列のみ**生成する。

    適用期は入力タイミング（今日）基準で解決するため、期の切替が月の途中にある月でも常に1列
    （active_term_case 参照）。該当期が無い月（期間の隙間）・期の業務名が未登録・旧データ
    （期間なし）は、入力不能にならないよう登録済みの担当業務すべての列にフォールバックする。
    """
    all_columns: list[tuple[int, dict]] = []
    for index in range(1, MAX_MAIN_TASKS + 1):
        column = _task_column(profile, "", "task_minutes_{index}", index)
        if column is not None:
            all_columns.append((index, column))
    case = active_term_case(profile, target_month)
    if case is not None:
        active_index = int(case.get("task_index") or 1)
        column = next((column for index, column in all_columns if index == active_index), None)
        if column is not None:
            return [column]
    return [column for _, column in all_columns]


def _sub_task_columns(profile: WorkAssignmentProfile) -> list[dict]:
    columns = []
    for index in range(1, MAX_SUB_TASKS + 1):
        column = _task_column(profile, "sub_", "sub_minutes_{index}", index)
        if column is not None:
            columns.append(column)
    return columns


def build_column_definition(profile: WorkAssignmentProfile, target_month: str | None = None) -> list[dict]:
    """契約の担当業務（対象月の期）→サブ業務（いずれも分のみ）と採点専用欄から報告書の列定義を生成する。

    コマ設定を未使用（use_period_slots=False）にした契約は担当時限列を生成しない（202607170831）。
    担当時限列の無い報告書は手入力方式（業務開始時間・各分を手入力→終了時間のみ自動計算）として
    講師フォーム・事務修正の双方で扱われる（クライアントは列スナップショットで判定）。
    """
    columns: list[dict] = [
        dict(c) for c in _LEADING_COLUMNS
        if profile.use_period_slots or c["key"] != "subject_period"
    ]
    columns += _main_task_columns(profile, target_month)
    columns += _sub_task_columns(profile)
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
