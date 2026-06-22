# イスト勤怠レポート（2システム構成）

FastAPI + PostgreSQL + Jinja2/Tailwind の実装。同一リポジトリに 2 システムを同梱しています。

| 区分 | 製品名 | 旧称 | コード | URL |
|---|---|---|---|---|
| 既存 | イスト勤怠レポート for 代々木進学会 | 指導実績報告システム | `backend/` | http://localhost:8000 |
| 新 | イスト勤怠レポート for EMPS | 業務連絡表システム | `new_backend/` | http://localhost:8001 |

詳細なドキュメントは **[`docs/README.md`](docs/README.md)（索引）** から辿ってください。

## 起動

```bash
cp .env.example .env
docker compose up -d --build
```

アクセス先:

- 既存システム（代々木進学会）: http://localhost:8000
- 新システム（EMPS）: http://localhost:8001
- API docs: http://localhost:8000/docs ・ http://localhost:8001/docs
- MailHog（開発時のメール受信確認）: http://localhost:8025

シード投入（デモデータ）:

```bash
docker compose exec backend python -m app.scripts.seed
```

## 開発用データリセット（開発環境専用）

報告書・ユーザー・招待データをリセットし、初期アカウントのみの状態に戻します。

```bash
# データのみリセット（コンテナ維持）
docker compose exec backend python -m app.scripts.dev_reset

# 完全リセット（DB ボリュームごと削除）
docker compose down -v
docker compose up -d --build
docker compose exec backend python -m app.scripts.seed
```

⚠ 本番環境では絶対に実行しないこと。

## デモアカウント

全員のパスワードは `Passw0rd!`。ロール別の詳細・新システムのアカウントは各システムの `docs/.../OPERATION_MANUAL.md` を参照してください。

| role | email |
|---|---|
| tutor | tutor1@example.com |
| tutor | tutor2@example.com |
| admin_receiver | receiver1@example.com |
| admin_reviewer | reviewer1@example.com |
| admin_master | master1@example.com |

## 注意

- Tailwind は Play CDN、認証は JWT を httpOnly Cookie（`access_token`）で保持。いずれもプロトタイプ簡略化のため、本番では Tailwind ビルド等への切替を検討してください。
- リマインダーは APScheduler で毎日 09:00 JST に実行されます。検証時は `backend/app/services/reminder_service.py` の cron 設定を一時的に interval へ変更すると MailHog で確認しやすくなります。
