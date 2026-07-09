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
    # メール配信方式: "smtp"(実送信) / "console"(ログ出力のみ・テスト/CI既定)。
    # テストや自動検証で実メールを大量送信しないよう既定は console。本番/開発は .env で smtp に設定する。
    MAIL_BACKEND: str = "console"
    # メールキュー（アウトボックス）のドレイナが「1通ごとにあける送信間隔（秒）」。
    # 同時送信・短時間連打によるスパム判定/ロックを防ぐためのレート制御。
    MAIL_SEND_INTERVAL_SECONDS: int = 4
    # 1回のドレイン実行で送る最大通数（これを超えたら次回の実行に回す）。
    MAIL_OUTBOX_BATCH_MAX: int = 20
    # 送信失敗時の最大試行回数（これに達したら failed として打ち切る）。
    MAIL_MAX_ATTEMPTS: int = 5
    REMINDER_DAYS_BEFORE_MONTH_END: int = 3
    # 学校承認の進捗メール（営業向けダイジェスト）を送る日＝「対象月の月末 + この日数」。
    # 例: 3 なら 6月分は 7/3 に送信。対象は全員承認が揃っていない学校のみ（月1回）。
    NEW_SCHOOL_PROGRESS_DAYS_AFTER_MONTH_END: int = 3

    model_config = {"env_file": "../.env", "extra": "ignore"}


settings = Settings()
