"""
フォーム定義レジストリ。フォームの差異はここへの登録のみで対応する。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LineColumn:
    key: str
    label: str
    type: str = "text"       # text / date / time / number
    summable: bool = False


@dataclass(frozen=True)
class FormDefinition:
    form_type: str
    label: str
    max_lines: int
    columns: tuple[LineColumn, ...]
    summable_keys: frozenset[str] = field(default_factory=frozenset)


FORM_REGISTRY: dict[str, FormDefinition] = {}


def _register(form: FormDefinition) -> FormDefinition:
    FORM_REGISTRY[form.form_type] = form
    return form


monthly_dispatch = _register(FormDefinition(
    form_type="monthly_dispatch",
    label="月次派遣報告",
    max_lines=26,
    columns=(
        LineColumn(key="date",           label="日付",         type="date"),
        LineColumn(key="start",          label="開始時刻",     type="time"),
        LineColumn(key="end",            label="終了時刻",     type="time"),
        LineColumn(key="subject_period", label="担当時限",     type="number"),
        LineColumn(key="teach_minutes",  label="数学科指導（分）", type="number", summable=True),
        LineColumn(key="break_minutes",  label="休憩時間（分）",   type="number", summable=True),
        LineColumn(key="commute_fee",    label="往復交通費（円）", type="number", summable=True),
        LineColumn(key="note",           label="内容",         type="text"),
    ),
    summable_keys=frozenset({"teach_minutes", "break_minutes", "commute_fee"}),
))


def get_form(form_type: str) -> FormDefinition:
    if form_type not in FORM_REGISTRY:
        raise KeyError(f"unknown form_type: '{form_type}'")
    return FORM_REGISTRY[form_type]
