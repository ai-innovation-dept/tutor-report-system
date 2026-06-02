import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator


class ReportCreate(BaseModel):
    assignment_id: uuid.UUID
    target_month: str
    form_type: str = "monthly_dispatch"
    form_data: dict = {}

    @field_validator("target_month")
    @classmethod
    def validate_month(cls, v: str) -> str:
        import re
        if not re.match(r"^\d{4}-\d{2}$", v):
            raise ValueError("target_month must be YYYY-MM")
        return v


class ReportPatch(BaseModel):
    form_data: dict


class WorkflowAction(BaseModel):
    action: str
    comment: str | None = None


class BulkReportAction(BaseModel):
    report_ids: list[uuid.UUID]
    action: str
    comment: str | None = None


class BulkReportActionOut(BaseModel):
    updated: int
    report_ids: list[uuid.UUID]


class MonthlySummaryOut(BaseModel):
    target_month: str | None
    total_reports: int
    status_counts: dict[str, int]
    total_teach_minutes: int
    total_break_minutes: int
    total_commute_fee: int


class ReportOut(BaseModel):
    id: uuid.UUID
    assignment_id: uuid.UUID
    tutor_id: uuid.UUID
    target_month: str
    form_type: str
    form_data: dict
    status: str
    current_approver_role: str | None
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReportEventOut(BaseModel):
    id: uuid.UUID
    report_id: uuid.UUID
    action: str
    from_status: str | None
    to_status: str | None
    comment: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
