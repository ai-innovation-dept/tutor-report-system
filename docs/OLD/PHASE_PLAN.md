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

## 実施済みの主な修正内容

2026-05-26 時点で、初期 Phase 0-10 以降に次の修正が反映済み。

| 区分 | 内容 | Main files |
|---|---|---|
| 承認管理 UI | 講師向け `/tutor/approval` を月次カード・進捗ステッパー中心に更新。差戻し再依頼、最終承認済み月のエクスポート導線を追加 | `templates/tutor/approval.html`, `api/reports.py`, `api/workflow.py` |
| 保護者確認 UI | 保護者の承認・差戻しを月次一括操作へ変更。保護者承認時は運営提出まで自動実行 | `templates/parent/reports.html`, `api/workflow.py`, `services/workflow_service.py` |
| 運営ダッシュボード | 受付、再鑑、最終承認を生徒・月単位のカードで操作。月次一括差戻しと全体エクスポートを追加 | `templates/admin/dashboard.html`, `api/workflow.py`, `api/reports.py` |
| エクスポート | `break_minutes` を反映した指導時間計算、CSV/Excel出力、複数生徒一括出力に対応 | `api/reports.py`, `templates/tutor/approval.html`, `templates/parent/reports.html`, `templates/admin/dashboard.html` |
| 休憩時間 | 報告書に `break_minutes` を追加し、作成・編集・出力・合計時間へ反映 | `models/entities.py`, `schemas/common.py`, `alembic/versions/0002_add_break_minutes.py` |
| 講師番号 | 講師に `tutor_no` を追加し、招待時に自動採番 | `models/entities.py`, `api/invitations.py`, `alembic/versions/0003_add_tutor_no.py` |
| 保護者未連携生徒 | 講師が保護者未設定の生徒を作成できるよう `assignments.parent_id` と `lesson_reports.parent_id` の NULL を許可 | `api/users.py`, `api/reports.py`, `alembic/versions/0004_allow_null_parent_in_assignments.py` |
| 招待方式登録 | 保護者、講師、運営スタッフを招待メール経由で登録する方式へ更新。招待トークンは72時間有効 | `api/invitations.py`, `api/auth.py`, `templates/register.html`, `templates/email/invitation*.txt` |
| ユーザー管理 | `/admin/users` を招待送信、招待一覧、登録済みユーザーの有効化・無効化中心に更新 | `templates/admin/users.html`, `api/users.py`, `api/invitations.py` |
| 紐付け管理 | `/admin/assignments` を既存生徒への講師追加と紐付け無効化中心に更新 | `templates/admin/assignments.html`, `api/users.py` |
| 通知 | 承認依頼、差戻し、保護者承認、運営提出、最終承認、招待メール、月末リマインダーを整備 | `services/notification_service.py`, `services/reminder_service.py`, `services/workflow_service.py`, `templates/email/*` |

## 将来対応事項

| 優先度 | 内容 | 備考 |
|---|---|---|
| High | Prompt C: 承認フロー変更 | 未実施。現在も DB ステータスは `submitted_to_admin` -> `received` -> `re_reviewed` -> `admin_approved` の運営3段階を保持している。UI では運営承認としてまとめて見せているが、バックエンドの承認フロー自体の統廃合は未対応 |
| Medium | 招待再送の監査強化 | 現在は同じ `POST /api/invitations` で未受諾招待を更新しトークン再発行する。再送履歴を別イベントとして残す余地がある |
| Medium | 通知チャネル拡張 | `notifications.channel` は email 以外も想定しているが、実送信はメールのみ |
| Medium | バックアップ・監査運用 | 手動バックアップ手順のみ。定期バックアップ、復旧訓練、監査ログ閲覧 UI は未実装 |
| Low | 外部マスタ連携 | ユーザー・担当紐付けの CSV/API 連携は未実装 |
