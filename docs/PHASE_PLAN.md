# Phase Plan

`codex_prompt.md` と対応する Phase 0 から Phase 10 の成果物をこのリポジトリへ配置しています。

| Phase | Title | Main files |
|---|---|---|
| 0 | 基盤・インフラ | `docker-compose.yml`, `backend/Dockerfile`, `app/main.py`, `app/config.py` |
| 1 | データベース層 | `app/database.py`, `app/models/entities.py`, `alembic/versions/0001_initial.py` |
| 2 | 認証・認可 | `core/security.py`, `core/rbac.py`, `api/auth.py`, `deps.py` |
| 3 | ユーザー管理 | `api/users.py`, `services/user_sync_service.py` |
| 4 | 指導報告書 CRUD | `api/reports.py` |
| 5 | 承認ワークフロー | `services/workflow_service.py`, `api/workflow.py` |
| 6 | アプリ内チャット | `api/chat.py`, `static/js/chat.js` |
| 7 | 通知・リマインダー | `services/notification_service.py`, `services/reminder_service.py`, `templates/email` |
| 8 | フロントエンド共通 | `templates/base.html`, `templates/login.html`, `static/js/auth.js` |
| 9 | ロール別画面 | `templates/tutor`, `templates/parent`, `templates/admin`, `api/pages.py` |
| 10 | シード・ドキュメント | `scripts/seed.py`, `README.md`, `docs/*` |

