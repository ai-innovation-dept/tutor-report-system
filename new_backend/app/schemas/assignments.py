import uuid
from datetime import datetime

from pydantic import BaseModel


class AssignmentCreate(BaseModel):
    tutor_id: uuid.UUID
    student_name: str


class AssignmentPatch(BaseModel):
    student_name: str | None = None
    is_active: bool | None = None
    parent_id: uuid.UUID | None = None
    skip_school_approval: bool | None = None
    reminder_enabled: bool | None = None
    reminder_days_after: int | None = None
    reminder_count: int | None = None


class TutorInfo(BaseModel):
    id: uuid.UUID
    display_name: str
    user_no: str | None = None
    tutor_no: str | None = None
    model_config = {"from_attributes": True}


class AssignmentOut(BaseModel):
    id: uuid.UUID
    tutor_id: uuid.UUID
    student_name: str
    is_active: bool
    system_type: str | None = None
    created_at: datetime
    tutor: TutorInfo | None = None
    form_type: str = "monthly_dispatch"
    form_definition: dict | None = None
    parent_id: uuid.UUID | None = None
    school_name: str | None = None
    skip_school_approval: bool = False
    reminder_enabled: bool = False
    reminder_days_after: int = 1
    reminder_count: int = 1

    model_config = {"from_attributes": True}
