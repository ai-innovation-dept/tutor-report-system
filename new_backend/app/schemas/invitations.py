import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str
    display_name: str | None = None


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    display_name: str | None = None
    user_no: str | None = None      # invitation.tutor_noカラムから
    expires_at: datetime
    accepted_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
