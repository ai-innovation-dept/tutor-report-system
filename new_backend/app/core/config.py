from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:postgres@db:5432/tutor"
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 8
    ENVIRONMENT: str = "development"
    TIMEZONE: str = "Asia/Tokyo"
    PORT: int = 8001
    # 旧システム(backend)と .env を共有するため、BASE_URL とは別名にして衝突を防ぐ
    NEW_BASE_URL: str = "http://localhost:8001"
    SMTP_HOST: str = "mailhog"
    SMTP_PORT: int = 1025
    # 送信経路（ホスト/ポート/認証/TLS）は旧システムと共通の .env 変数を共有する（同一SMTPリレー）
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    # 送信時のTLS方式: none(開発/MailHog) / starttls(通常587番) / ssl(暗黙TLS・通常465番)
    SMTP_TLS: str = "none"
    # 送信元アドレス（仮）。旧システムと .env を共有するため SMTP_FROM とは別名にして衝突を防ぐ
    NEW_SMTP_FROM: str = "noreply@work-system.local"
    REMINDER_DAYS_BEFORE_MONTH_END: int = 3

    model_config = {"env_file": "../.env", "extra": "ignore"}


settings = Settings()
