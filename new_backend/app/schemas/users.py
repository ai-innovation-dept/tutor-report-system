import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    role: str
    roles: list[str] = []
    tutor_no: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}
