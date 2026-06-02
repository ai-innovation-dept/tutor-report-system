from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@db:5432/tutor"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 8
    ENVIRONMENT: str = "development"
    TIMEZONE: str = "Asia/Tokyo"
    PORT: int = 8001

    model_config = {"env_file": "../.env", "extra": "ignore"}


settings = Settings()
