# === Phase 0: 基盤・インフラ START ===
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/tutor"
    jwt_secret: str = "dev-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_hours: int = 8
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@example.com"
    base_url: str = "http://localhost:8000"
    reminder_days_before_month_end: int = 3
    timezone: str = "Asia/Tokyo"
    cors_origins: str = ""
    auto_create_tables: bool = Field(False, description="Test/dev fallback; migrations are primary.")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
# === Phase 0 END ===
