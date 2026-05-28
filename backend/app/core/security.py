# === Phase 2: 認証・認可 START ===
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=settings.access_token_expire_hours))
    return jwt.encode({"sub": subject, "exp": expire}, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    # SSO extension point: add SsoAuthenticator and dispatch here or from api/auth.py.
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True), User.deleted_at.is_(None)))
    if not user or not verify_password(password, user.password_hash):
        return None
    return user
# === Phase 2 END ===
