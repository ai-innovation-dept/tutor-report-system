# 家庭教師 指導実績報告システム

FastAPI + PostgreSQL + Jinja2/Tailwind CDN のプロトタイプです。

## 起動

```bash
cp .env.example .env
docker compose up --build
```

アクセス先:

- アプリ: http://localhost:8000
- API docs: http://localhost:8000/docs
- MailHog: http://localhost:8025

シード投入:

```bash
docker compose exec backend python -m app.scripts.seed
```

## 開発用データリセット（開発環境専用）

報告書ユーザー招待データをすべて削除し、
初期アカウントのみの状態に戻します。

```bash
# データのみリセット（コンテナ維持）
docker compose exec backend python -m app.scripts.dev_reset

# 完全リセット（DB ボリュームごと削除）
docker compose down -v
docker compose up -d --build
docker compose exec backend python -m app.scripts.seed
```

本番環境では絶対に実行しないこと

## デモアカウント

全員のパスワードは `Passw0rd!` です。

| role | email |
|---|---|
| tutor | tutor1@example.com |
| tutor | tutor2@example.com |
| admin_receiver | receiver1@example.com |
| admin_reviewer | reviewer1@example.com |
| admin_master | master1@example.com |

## 注意

Tailwind は Play CDN、JWT は localStorage と httpOnly Cookie の併用です。どちらもプロトタイプ簡略化で、本番では Tailwind ビルドとリフレッシュトークン方式へ切り替えてください。

リマインダーは APScheduler で毎日 09:00 JST に実行されます。検証時は `backend/app/services/reminder_service.py` の cron 設定を一時的に interval へ変更すると MailHog で確認しやすくなります。
