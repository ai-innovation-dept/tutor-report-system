"""契約管理（work_assignment_profiles）の入出力スキーマ。"""
import re
import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_validator, model_validator

# 委託業務は担当業務（①〜③・①必須）と副業務（①〜⑤・任意）の2区分。
# データ上の区分名は main / sub のまま（表示名のみ担当業務／副業務）。
MAX_MAIN_TASKS = 3
MAX_SUB_TASKS = 5
# コマ設定（担当時限の時間割）は最大10コマ（講師フォームの担当時限①〜⑩に対応）
MAX_PERIOD_SLOTS = 10

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


class ContractWorkloadCase(BaseModel):
    """月時間（分）・週コマの期間付きケース。

    task_index で担当業務①〜③に紐づく（超過判定・要望連絡事項の業務別表示に使用）。
    旧データ互換のため task_index 無し（None）も許容する。
    """
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    task_index: int | None = None  # 担当業務①〜③（1..3）

    @field_validator("task_index")
    @classmethod
    def validate_task_index(cls, v: int | None) -> int | None:
        if v is not None and not (1 <= v <= MAX_MAIN_TASKS):
            raise ValueError(f"task_index は1〜{MAX_MAIN_TASKS}で指定してください")
        return v

    def is_empty(self) -> bool:
        return (
            self.monthly_minutes is None
            and self.weekly_lessons is None
            and self.start_date is None
            and self.end_date is None
        )


class ContractBase(BaseModel):
    customer_id: str | None = None
    our_staff: str | None = None
    dispatch_place_address: str | None = None
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
    tasks: list[ContractTask] = []        # メイン業務（①必須・最大3件）
    sub_tasks: list[ContractTask] = []    # サブ業務（任意・最大5件）
    # コマ設定（担当時限の時間割・最大10）。①から順・時間の重なり不可。
    period_slots: list[ContractPeriodSlot] = []

    @field_validator("period_slots")
    @classmethod
    def validate_period_slots(cls, value: list[ContractPeriodSlot]) -> list[ContractPeriodSlot]:
        if len(value) > MAX_PERIOD_SLOTS:
            raise ValueError(f"コマ設定は最大{MAX_PERIOD_SLOTS}コマです")
        for index in range(1, len(value)):
            if value[index].start < value[index - 1].end:
                raise ValueError(f"コマ{index + 1}が前のコマと時間が重なっています")
        return value

    @model_validator(mode="after")
    def validate_period_slots_with_flags(self) -> "ContractBase":
        # 休憩時間（分）列が非表示だと「隙間→休憩」の自動計算が成立しないため併用不可
        if self.period_slots and self.show_break_minutes is False:
            raise ValueError("休憩時間を非表示にしている契約ではコマ設定を使用できません（表示項目の「休憩時間」をONにしてください）")
        return self

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, value: list[ContractTask]) -> list[ContractTask]:
        non_empty = [task for task in value if not task.is_empty()]
        if len(value) > MAX_MAIN_TASKS:
            raise ValueError(f"担当業務は最大{MAX_MAIN_TASKS}件です")
        return non_empty

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
    # 「委託業務①必須」はエンドポイント側で検証する（空行除去後の件数で判定するため）


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
