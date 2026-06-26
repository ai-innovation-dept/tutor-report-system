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
    skip_parent_approval: bool = False
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserPatch(BaseModel):
    display_name: str | None = None
    is_active: bool | None = None
    allowed_systems: list[str] | None = None
    skip_parent_approval: bool | None = None


class UserRolesPatch(BaseModel):
    roles: list[str]


class UserListOut(BaseModel):
    items: list[UserOut]
    total: int
    page: int = 1
    per_page: int = 50
    total_pages: int = 1
    role_counts: dict[str, int] = {}
    active_admin_master_count: int = 0
    active_admin_chief_count: int = 0
    # ロール別の有効ユーザー数（最後の1人なら削除・無効化を不可にするUI判定に使用）
    active_role_counts: dict[str, int] = {}
