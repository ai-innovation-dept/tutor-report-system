from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@db:5432/tutor"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 8
    ENVIRONMENT: str = "development"
    TIMEZONE: str = "Asia/Tokyo"
    PORT: int = 8001
    BASE_URL: str = "http://localhost:8001"
    SMTP_HOST: str = "mailhog"
    SMTP_PORT: int = 1025
    SMTP_FROM: str = "noreply@work-system.local"
    REMINDER_DAYS_BEFORE_MONTH_END: int = 3

    model_config = {"env_file": "../.env", "extra": "ignore"}


settings = Settings()
