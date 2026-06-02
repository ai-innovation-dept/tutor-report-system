from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token
from app.dependencies.auth import get_current_user, get_active_role
from app.schemas.auth import LoginRequest, RoleSelectRequest, TokenResponse
from app.schemas.users import UserOut
from app.services.user_service import authenticate, effective_roles, has_new_system_role

router = APIRouter(prefix="/api/auth", tags=["auth"])

ROLE_LABELS = {
    "tutor": "講師",
    "school": "学校担当",
    "sales": "営業",
    "office": "事務",
    "admin_master": "管理者",
}

_DASHBOARD = {
    "tutor": "/w/tutor/dashboard",
    "school": "/w/school/dashboard",
    "sales": "/w/sales/dashboard",
    "office": "/w/office/dashboard",
    "admin_master": "/w/admin/dashboard",
}


def _dashboard(role: str) -> str:
    return _DASHBOARD.get(role, "/w/")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, str(payload.username), payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    if not has_new_system_role(user):
        raise HTTPException(status_code=403, detail="new system access not granted")

    roles = effective_roles(user)
    token = create_access_token({"sub": str(user.id)})
    response.set_cookie(key="access_token", value=token, httponly=True, samesite="lax")

    if len(roles) == 1:
        response.set_cookie(key="selected_role", value=roles[0], httponly=True, samesite="lax")
        return TokenResponse(
            access_token=token,
            role=roles[0],
            roles=roles,
            redirect_url=_dashboard(roles[0]),
        )
    return TokenResponse(access_token=token, roles=roles, redirect_url="/w/select-role")


@router.post("/select-role", response_model=TokenResponse)
def select_role(
    payload: RoleSelectRequest,
    response: Response,
    user=Depends(get_current_user),
):
    roles = effective_roles(user)
    if payload.role not in roles:
        raise HTTPException(status_code=403, detail="role not available")
    response.set_cookie(key="selected_role", value=payload.role, httponly=True, samesite="lax")
    return TokenResponse(
        access_token="",
        role=payload.role,
        roles=roles,
        redirect_url=_dashboard(payload.role),
    )


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    response.delete_cookie("selected_role")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user=Depends(get_current_user)):
    return user
