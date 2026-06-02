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
    user_no: str | None = None
    allowed_systems: list[str] | None = None
    is_active: bool

    model_config = {"from_attributes": True}


class UserPatch(BaseModel):
    display_name: str | None = None
    is_active: bool | None = None
    allowed_systems: list[str] | None = None


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int
