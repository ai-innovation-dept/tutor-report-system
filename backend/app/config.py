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
    # 送信時のTLS方式: none(開発/MailHog) / starttls(通常587番) / ssl(暗黙TLS・通常465番)
    smtp_tls: str = "none"
    # メール配信方式: "smtp"(実送信) / "console"(ログ出力のみ・テスト/CI既定)。
    # 自動テストで実メールを大量送信しないよう既定は console。本番/開発は .env で smtp に設定。
    mail_backend: str = "console"
    # メールキュー（アウトボックス）のドレイナが「1通ごとにあける送信間隔（秒）」。
    # 同時送信・短時間連打によるスパム判定/ロックを防ぐためのレート制御。
    mail_send_interval_seconds: int = 4
    # 1回のドレイン実行で送る最大通数 / 送信失敗時の最大試行回数。
    mail_outbox_batch_max: int = 20
    mail_max_attempts: int = 5
    base_url: str = "http://localhost:8000"
    reminder_days_before_month_end: int = 3
    # === 提出締切通知（講師向け・改修依頼 202607161428） ===
    # メール送信の有効化フラグ。誤送信防止のため既定は無効。
    # 本番で .env に DEADLINE_NOTICE_ENABLED=true を設定して有効化する（画面バナーは常時表示）。
    deadline_notice_enabled: bool = False
    # 1回目（月中）通知の開始日（毎月N日）。提出締切は対象月の翌月第一営業日。
    deadline_notice_midmonth_day: int = 15
    # 営業日判定で休業扱いにする日（MM-DD をカンマ区切り・毎年適用）。既定は年末年始休業。
    # 土日・日本の祝日は自動で休業扱い（jpholiday）のため、ここには会社独自の休業日のみ書く。
    business_closed_days: str = "12-29,12-30,12-31,01-01,01-02,01-03"
    timezone: str = "Asia/Tokyo"
    cors_origins: str = ""
    auto_create_tables: bool = Field(False, description="Test/dev fallback; migrations are primary.")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
# === Phase 0 END ===
