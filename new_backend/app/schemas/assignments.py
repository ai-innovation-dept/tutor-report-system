import uuid
from datetime import datetime

from pydantic import BaseModel


class AssignmentCreate(BaseModel):
    tutor_id: uuid.UUID
    student_name: str


class AssignmentPatch(BaseModel):
    student_name: str | None = None
    is_active: bool | None = None


class TutorInfo(BaseModel):
    id: uuid.UUID
    display_name: str
    user_no: str | None = None
    model_config = {"from_attributes": True}


class AssignmentOut(BaseModel):
    id: uuid.UUID
    tutor_id: uuid.UUID
    student_name: str
    is_active: bool
    system_type: str | None = None
    created_at: datetime
    tutor: TutorInfo | None = None

    model_config = {"from_attributes": True}
