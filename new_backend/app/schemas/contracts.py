"""契約管理（work_assignment_profiles）の入出力スキーマ。"""
import re
import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_validator, model_validator

# 委託業務は担当業務（前期・後期の2本・いずれも必須）と副業務（①〜⑤・任意）の2区分。
# 担当業務はデータ上 task_index 1=前期 / 2=後期 として task_name_1/2 等の列に格納する
# （旧仕様①〜③の名残で task_name_3 列は残るが、新規保存では使用しない）。
MAX_MAIN_TASKS = 2
MAX_SUB_TASKS = 5
# コマ設定（担当時限の時間割）は最大10コマ（講師フォームの担当時限①〜⑩に対応）
MAX_PERIOD_SLOTS = 10
# 前期/後期の表示ラベル（task_index → ラベル）。要望連絡事項・エラーメッセージで共用する。
TERM_LABELS = {1: "前期", 2: "後期"}

_SLOT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ContractTask(BaseModel):
    """委託業務（業務名・委託業務ID・個別契約ID）。常に「分のみ」で報告書に反映。"""
    task_name: str | None = None
    task_id: str | None = None
    contract_id: str | None = None

    def is_empty(self) -> bool:
        return not any([
            (self.task_name or "").strip(),
            (self.task_id or "").strip(),
            (self.contract_id or "").strip(),
        ])


class ContractPeriodSlot(BaseModel):
    """コマ設定（担当時限の時間割）の1コマ。時刻は 'HH:MM'（例 "08:30"〜"09:20"）。

    リストの並び順＝コマ番号①〜⑩。設定がある契約は講師フォームで
    選択コマから業務開始・担当業務（分）・休憩時間（分）を自動計算する。
    """
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, v: str) -> str:
        if not _SLOT_TIME_RE.match(v or ""):
            raise ValueError("コマの時刻は HH:MM 形式（00:00〜23:59）で指定してください")
        return v

    @model_validator(mode="after")
    def validate_range(self) -> "ContractPeriodSlot":
        if self.end <= self.start:
            raise ValueError("コマの終了時刻は開始時刻より後にしてください")
        return self


def _validate_slot_list(value: list[ContractPeriodSlot]) -> list[ContractPeriodSlot]:
    """コマ設定リストの共通検証（最大数・前のコマとの重なり）。契約単位・期単位で共用。"""
    if len(value) > MAX_PERIOD_SLOTS:
        raise ValueError(f"コマ設定は最大{MAX_PERIOD_SLOTS}コマです")
    for index in range(1, len(value)):
        if value[index].start < value[index - 1].end:
            raise ValueError(f"コマ{index + 1}が前のコマと時間が重なっています")
    return value


class ContractWorkloadCase(BaseModel):
    """担当業務（前期/後期）の期別設定＝月時間（分）・週コマ・適用期間・コマ設定。

    task_index で担当業務（1=前期 / 2=後期）に紐づく。適用期間は超過判定・
    講師フォームの入力フォーマット（期のコマ設定）・要望連絡事項の表示に使用する。
    旧データ互換のため task_index 無し（None）・期間無しも読み込みは許容する
    （新規保存時の必須検証は term_payload_errors で行う）。
    """
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    task_index: int | None = None  # 1=前期 / 2=後期
    # 期別のコマ設定（担当時限の時間割）。空リストはコマ設定なし（従来の8:40固定ルール）。
    slots: list[ContractPeriodSlot] = []

    @field_validator("task_index")
    @classmethod
    def validate_task_index(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= MAX_MAIN_TASKS):
            raise ValueError(f"task_index は1〜{MAX_MAIN_TASKS}（1=前期／2=後期）で指定してください")
        return v

    @field_validator("slots")
    @classmethod
    def validate_slots(cls, value: list[ContractPeriodSlot]) -> list[ContractPeriodSlot]:
        return _validate_slot_list(value)

    def is_empty(self) -> bool:
        return (
            self.monthly_minutes is None
            and self.weekly_lessons is None
            and self.start_date is None
            and self.end_date is None
            and not self.slots
        )


def term_payload_errors(tasks: list[ContractTask], workload_cases: list[ContractWorkloadCase]) -> list[str]:
    """担当業務（前期・後期）の必須検証（画面保存・CSV取込・APIで共用）。

    - 前期・後期とも委託業務名（またはID）と適用期間（開始・終了）が必須
    - 各期の期別設定（ケース）は1件まで
    - 前期・後期の適用期間は重複不可
    戻り値はエラーメッセージのリスト（空＝妥当）。
    """
    errors: list[str] = []
    cases_by_index: dict[int, list[ContractWorkloadCase]] = {}
    for case in workload_cases:
        cases_by_index.setdefault(case.task_index or 1, []).append(case)

    for index in range(1, MAX_MAIN_TASKS + 1):
        label = TERM_LABELS[index]
        task = tasks[index - 1] if len(tasks) >= index else None
        # 報告書の列は委託業務名から生成されるため、ID類だけでなく名称そのものを必須とする
        if task is None or not (task.task_name or "").strip():
            errors.append(f"担当業務（{label}）の委託業務名は必須です")
        cases = cases_by_index.get(index, [])
        if len(cases) > 1:
            errors.append(f"担当業務（{label}）の月時間・週コマ・適用期間は1件のみ設定できます")
        case = cases[0] if cases else None
        if case is None or not (case.start_date and case.end_date):
            errors.append(f"担当業務（{label}）の適用期間（開始日・終了日）は必須です")

    first = next(iter(cases_by_index.get(1, [])), None)
    second = next(iter(cases_by_index.get(2, [])), None)
    if (
        first is not None and second is not None
        and first.start_date and first.end_date and second.start_date and second.end_date
        and first.start_date <= second.end_date and second.start_date <= first.end_date
    ):
        errors.append("担当業務（前期）と（後期）の適用期間が重複しています")
    return errors


class ContractBase(BaseModel):
    customer_id: str | None = None
    our_staff: str | None = None
    dispatch_place_address: str | None = None
    # 就業場所（報告書の「事業所の所在地」の下に表示・講師読取専用）
    work_location: str | None = None
    # 教室名（報告書の「事業所の名称」の隣に表示・講師読取専用）
    classroom_name: str | None = None
    # 報告書フォームの項目表示/非表示（既定は全て表示）。契約からライブ反映する。
    show_dispatch_address: bool = True
    show_work_content: bool = True
    show_commuter_pass: bool = True
    show_break_minutes: bool = True
    show_schedule_note: bool = True
    contract_start: date | None = None
    contract_end: date | None = None
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    workload_cases: list[ContractWorkloadCase] = []
    shift_note: str | None = None
    work_content: str | None = None
    # 採点専用欄（回数＋分数固定）。enabled のときのみ報告書に「{項目名}（{単位}）」列を生成する。
    # 項目名・単位は任意入力（既定: 採点／回）。分は固定。
    scoring_enabled: bool = False
    scoring_label: str | None = None
    scoring_unit: str | None = None
    scoring_task_id: str | None = None
    scoring_contract_id: str | None = None
    # 担当業務（位置固定: [0]=前期 / [1]=後期）。空判定・必須検証は term_payload_errors で行う。
    tasks: list[ContractTask] = []
    sub_tasks: list[ContractTask] = []    # サブ業務（任意・最大5件）
    # 旧形式: 契約単位のコマ設定（新規保存では期別 workload_cases.slots を使用。読込互換のため残す）。
    period_slots: list[ContractPeriodSlot] = []

    @field_validator("period_slots")
    @classmethod
    def validate_period_slots(cls, value: list[ContractPeriodSlot]) -> list[ContractPeriodSlot]:
        return _validate_slot_list(value)

    @model_validator(mode="after")
    def validate_period_slots_with_flags(self) -> "ContractBase":
        # 休憩時間（分）列が非表示だと「隙間→休憩」の自動計算が成立しないため併用不可
        has_slots = bool(self.period_slots) or any(case.slots for case in self.workload_cases)
        if has_slots and self.show_break_minutes is False:
            raise ValueError("休憩時間を非表示にしている契約ではコマ設定を使用できません（表示項目の「休憩時間」をONにしてください）")
        return self

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, value: list[ContractTask]) -> list[ContractTask]:
        # 前期/後期は位置で判定するため空行を詰めない（[0]=前期・[1]=後期）。末尾の空行のみ削除。
        if len(value) > MAX_MAIN_TASKS:
            raise ValueError(f"担当業務は前期・後期の{MAX_MAIN_TASKS}件です")
        trimmed = list(value)
        while trimmed and trimmed[-1].is_empty():
            trimmed.pop()
        return trimmed

    @field_validator("sub_tasks")
    @classmethod
    def validate_sub_tasks(cls, value: list[ContractTask]) -> list[ContractTask]:
        non_empty = [task for task in value if not task.is_empty()]
        if len(value) > MAX_SUB_TASKS:
            raise ValueError(f"副業務は最大{MAX_SUB_TASKS}件です")
        return non_empty

    @field_validator("workload_cases")
    @classmethod
    def validate_workload_cases(cls, value: list[ContractWorkloadCase]) -> list[ContractWorkloadCase]:
        non_empty = [case for case in value if not case.is_empty()]
        for case in non_empty:
            if case.start_date and case.end_date and case.end_date < case.start_date:
                raise ValueError("月時間・週コマの適用期間は終了日を開始日以降にしてください")
        return non_empty


class ContractCreate(ContractBase):
    tutor_id: uuid.UUID
    school_id: uuid.UUID
    # 「前期・後期の必須」はエンドポイント側で term_payload_errors により検証する
    # （部分更新(PATCH)と検証条件を揃えるため、モデル検証には含めない）


class ContractUpdate(ContractBase):
    tutor_id: uuid.UUID | None = None
    school_id: uuid.UUID | None = None


class ContractForTutorOut(BaseModel):
    """講師の報告書フォームへ自動反映するための契約情報＋動的列定義。"""
    school_id: uuid.UUID
    school_name: str | None = None
    customer_id: str | None = None
    our_staff: str | None = None
    dispatch_place_address: str | None = None
    work_location: str | None = None
    classroom_name: str | None = None
    show_dispatch_address: bool = True
    show_work_content: bool = True
    show_commuter_pass: bool = True
    show_break_minutes: bool = True
    show_schedule_note: bool = True
    contract_start: date | None = None
    contract_end: date | None = None
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    workload_cases: list[ContractWorkloadCase] = []
    shift_note: str | None = None
    work_content: str | None = None
    tasks: list[ContractTask] = []
    sub_tasks: list[ContractTask] = []
    period_slots: list[ContractPeriodSlot] = []
    column_definition: list[dict] = []


class ContractOut(BaseModel):
    id: uuid.UUID
    assignment_id: uuid.UUID
    tutor_id: uuid.UUID
    school_id: uuid.UUID
    tutor_name: str | None = None
    school_name: str | None = None
    customer_id: str | None = None
    our_staff: str | None = None
    dispatch_place_address: str | None = None
    work_location: str | None = None
    classroom_name: str | None = None
    show_dispatch_address: bool = True
    show_work_content: bool = True
    show_commuter_pass: bool = True
    show_break_minutes: bool = True
    show_schedule_note: bool = True
    contract_start: date | None = None
    contract_end: date | None = None
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    workload_cases: list[ContractWorkloadCase] = []
    shift_note: str | None = None
    work_content: str | None = None
    scoring_enabled: bool = False
    scoring_label: str | None = None
    scoring_unit: str | None = None
    scoring_task_id: str | None = None
    scoring_contract_id: str | None = None
    tasks: list[ContractTask] = []
    sub_tasks: list[ContractTask] = []
    period_slots: list[ContractPeriodSlot] = []
    is_active: bool
    created_at: datetime
    updated_at: datetime
