"""契約管理（work_assignment_profiles）の入出力スキーマ。"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_validator

MAX_TASKS = 5


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


class ContractBase(BaseModel):
    customer_id: str | None = None
    our_staff: str | None = None
    contract_start: date | None = None
    contract_end: date | None = None
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    shift_note: str | None = None
    work_content: str | None = None
    # 採点専用欄（回数＋分数固定）。enabled のときのみ報告書に「{項目名}（{単位}）」列を生成する。
    # 項目名・単位は任意入力（既定: 採点／回）。分は固定。
    scoring_enabled: bool = False
    scoring_label: str | None = None
    scoring_unit: str | None = None
    scoring_task_id: str | None = None
    scoring_contract_id: str | None = None
    tasks: list[ContractTask] = []

    @field_validator("tasks")
    @classmethod
    def validate_tasks(cls, value: list[ContractTask]) -> list[ContractTask]:
        non_empty = [task for task in value if not task.is_empty()]
        if len(value) > MAX_TASKS:
            raise ValueError(f"委託業務は最大{MAX_TASKS}件です")
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
    contract_start: date | None = None
    contract_end: date | None = None
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    shift_note: str | None = None
    work_content: str | None = None
    tasks: list[ContractTask] = []
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
    contract_start: date | None = None
    contract_end: date | None = None
    monthly_minutes: int | None = None
    weekly_lessons: int | None = None
    shift_note: str | None = None
    work_content: str | None = None
    scoring_enabled: bool = False
    scoring_label: str | None = None
    scoring_unit: str | None = None
    scoring_task_id: str | None = None
    scoring_contract_id: str | None = None
    tasks: list[ContractTask] = []
    is_active: bool
    created_at: datetime
    updated_at: datetime
