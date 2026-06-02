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


class ResetTokenInfoOut(BaseModel):
    valid: bool
    reason: str | None = None
    email: str | None = None
