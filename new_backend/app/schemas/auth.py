from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    username: EmailStr
    password: str


class RoleSelectRequest(BaseModel):
    role: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str | None = None
    roles: list[str] = []
    redirect_url: str = "/"


class RegisterInfoOut(BaseModel):
    email: str
    role: str
    role_display: str
    display_name: str | None = None
    user_no: str | None = None


class RegisterIn(BaseModel):
    token: str
    password: str
    display_name: str | None = None


class RegisterOut(BaseModel):
    message: str


class ForgotPasswordIn(BaseModel):
    email: EmailStr


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


class ChangePasswordIn(BaseModel):
    new_password: str
    current_password: str | None = None  # 任意変更時のみ必須（初回強制変更時は不要）


class ResetTokenInfoOut(BaseModel):
    valid: bool
    reason: str | None = None
    email: str | None = None
