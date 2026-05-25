# === Phase 2: 認証・認可 START ===
from fastapi import Depends, HTTPException, status

from app.deps import get_current_user
from app.models import User


ADMIN_ROLES = {"admin_receiver", "admin_reviewer", "admin_master"}


def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role")
        return user
    return dependency
# === Phase 2 END ===

