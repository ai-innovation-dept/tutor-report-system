"""契約管理（work_assignment_profiles）の入出力スキーマ。"""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, field_validator

MAX_TASKS = 5


class ContractTask(BaseModel):
    """委託業務（業務名・委託業務ID・個別契約ID）。"""
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
    has_scoring: bool = False
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
    has_scoring: bool = False
    tasks: list[ContractTask] = []
    is_active: bool
    created_at: datetime
    updated_at: datetime
