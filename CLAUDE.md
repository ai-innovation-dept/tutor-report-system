# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ドキュメント（まず読む）

本リポジトリは「**イスト勤怠レポート**」の **2システム構成**。ドキュメントは **`docs/README.md`（索引）** から辿る。システム別の仕様・操作手順は `docs/イスト勤怠レポート for 代々木進学会/`・`docs/イスト勤怠レポート for EMPS/` に、共通のデータモデル／インフラ／引継ぎは `docs/` 直下にある。

| 区分 | 製品名 | 旧称 | コード | ポート |
|---|---|---|---|---|
| 既存 | イスト勤怠レポート for 代々木進学会 | 指導実績報告システム | `backend/` | 8000 |
| 新 | イスト勤怠レポート for EMPS | 業務連絡表システム | `new_backend/` | 8001 |

## 引継ぎ・現在の作業状況（新しく入った担当者・別アカウントはまず読む）

進行中の作業の引継ぎ・未対応事項・本番反映状況は **`docs/HANDOFF.md`** に集約している。作業を引き継ぐ場合はまず `docs/HANDOFF.md` を読むこと。

## 改修依頼の標準ルール（毎回の【改修時の留意事項】は貼り付け不要）

改修依頼のたびに貼り付けていた【改修時の留意事項】は、以下として**常に適用**する。依頼者は
**改修内容だけ**を書けばよい（管理番号の指定も、留意事項の再掲も不要。今回だけ方針を変えたいときのみ都度明記）。
Claude Code は改修・調査の着手時に本ルールを必ず適用し、**完了時に末尾の「完了時セルフチェック」を出力**して
漏れを防ぐ。

### 0. 管理番号は Claude Code が発行する
- 依頼者は管理番号を指定しない。**着手時点の日本時刻(JST)を `YYYYMMDDHHMM`（12桁・分単位）** として
  Claude Code が採番する（例: 2026-07-22 16:51 → `202607221651`）。以後この番号をコミット・PR・テスト名・
  コード内コメント・memory・`docs/HANDOFF.md` で一貫して使い、**結果の冒頭に「管理番号: NNNN」を明記**する。
- JST 取得は**タイムゾーン明示**で行う（⚠️ この環境の Git Bash `date` は UTC を返すため使わない）。次のいずれか:
  - PowerShell: `[System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow,"Tokyo Standard Time").ToString("yyyyMMddHHmm")`
  - プロジェクトの権威実装: `docker compose exec -T backend python -c "from app.core.time import get_current_jst; print(get_current_jst().strftime('%Y%m%d%H%M'))"`
- 前回の分割・積み残しの続きなど、**既存番号のある作業を継続する場合は新規採番せず元番号を引き継ぐ**
  （memory / HANDOFF / git ログから特定）。同一分に複数依頼が重なったら末尾に枝番（`-2`）を付す。

### 1. 進め方（優先順位・分割・確認）
- 複数項目は**優先順位**をつけて着手。1回の処理で品質が落ちそうなら**分割**し、**実施した項目番号**と
  **積み残し**を結果に明記する（積み残しは HANDOFF に記録し次回へ）。
- **要件レベルの不明点は必ず確認**（憶測で作業しない）。確認は選択式ではなく**文章でオープンに質問**する。
  一方、**設計の細部は妥当な既定を宣言して進める**（迷って止めない）。
- 既存の実装・命名・設計思想を**先に調べてそれに倣う**（車輪の再発明・独自流儀を持ち込まない）。

### 2. デザイン・UI（レイアウト／入力フォーム／ポップアップ）
「**見やすさ・操作性・統一性・操作に迷わない設計**」を最優先に、**既存の意匠へ合わせる**こと。
- **統一性（最重要）**: 既存の共通トークン／コンポーネントを再利用する（例: ページ見出し
  `h1.text-xl.font-bold.text-slate-800` ＋説明 `p.mt-1.text-sm.text-slate-500`、主ボタン=emerald、
  状態=バッジ、詳細/ロール変更=ドロワー、`<details>` アコーディオン＋シェブロン）。**新しい色・余白・部品を
  勝手に作らない**。姉妹画面（同機能の別画面・もう一方のシステム）と見た目・語彙・操作を揃える。
- **2システム間パリティ**: legacy(代々木)と EMPS で同機能の画面は、**特段の理由がない限り同一構成**にする
  （片方だけ良くしない。差異を残すならコメントで理由を明記）。
- **入力フォーム**: ラベル上置き＋入力欄と下端揃え（`items-end`）、関連項目は grid で整列、幅は
  `minmax(0,1fr)`／全幅で右端をはみ出させない、ボタンは入力欄と同じ高さ（`h-[42px]`）。**必須はフロントで
  強制**しつつ API 層は互換維持。**エラーは日本語で具体的・次の操作が分かる文言**に。空欄を 0 埋めしない。
- **ポップアップ／モーダル**: 構造を統一（見出し／本文／操作ボタン）。**確認ポップアップは必ず1つだけ**
  （二重確認を出さない＝既知の要注意点）。overlay クリック／ESC／キャンセルで**必ず抜けられる**、主操作を
  視覚的に際立たせ、破壊的操作は確認を挟む。`role="dialog"`・`aria-*`・フォーカス移動を付ける。
- **操作に迷わない設計**: 主要導線を1つに絞る、現在地（見出し・パンくず・状態バッジ）を示す、破壊的操作と
  キャンセルを明確に分ける、曖昧なラベルを避ける、操作結果（成功／失敗）を必ずフィードバックする。
- **レスポンシブ**: `<768px` は既存のカード化（`.mobile-cards`）に合わせ、**タップ領域44pxはスマホのみ**、
  横スクロールを出さない。**PC幅とスマホ幅の両方で目視**する（`xl=1280` は 1366px 画面を含む罠に注意）。
- 画面 HTML を変えたら**ページ文字列検証テストが落ちうる**ため、必ずフル pytest を通す（§4）。

### 3. 影響範囲・連携の徹底追跡（スパゲッティ回避）
着手前に**データの流れを端から端まで洗い出し**、影響点を列挙してから実装する。
- **複製ペアの同時更新（最重要）**: 本リポジトリは legacy と EMPS が**コードを複製し合う**箇所が多い
  （計算コアの JS/Py ペア、テンプレの複製、メール outbox ペア、ボール対応表の4テンプレ複製、通知サービスの
  対 等）。**片側だけ直すの禁止＝対になる全コピーを同時更新**する。着手前に**まず複製相手を探す**。
- **タッチポイント全列挙**: 入力フォーム → API/スキーマ → サービス → モデル/DB → 表示（テンプレ／PDF／CSV／
  メール／アプリ内通知／監査イベント）→ 他画面、の順に波及箇所を**全部**挙げてから着手（例: 項目追加は
  CSV 列・PDF ラベル・参照画面・他システムまで漏れなく）。
- **共有リソース**: 共有 `.env`（URL は BASE_URL/NEW_BASE_URL に分離）、共有テーブル `users`/`assignments`
  （`system_type`/`allowed_systems` で自系のみ絞る）、共有メールキューの直列化 を壊さないか確認。
- **DB 変更**: スキーマ変更は Alembic マイグレーション追加（番号を明記・両システムの migration ディレクトリを
  取り違えない）。データ移行の要否も判断。
- **連携先への波及**: 変更が PDF/CSV(入出力)・メール文面・API 利用側・スケジューラ・他システムに及ぶかを
  判定し、**及ぶ場合は連携先の改修要否まで結論づけて**から進める（及ばないなら「影響なし」と明記）。

### 4. テスト（本番メールを送らない）
- 開発後は**必ず要件充足を自動テストで確認**（curl や JS 構文チェックだけで済ませない）。
- **本番メールを一切送らない手段**で行う。承認等の操作は実メールを送出しうるため、検証は
  **自動テスト（pytest, `MAIL_BACKEND=console` 既定＝実送信ゼロ）**で行い、実 SMTP の dev 環境で
  承認フローを手動実行しない。メール検証は送信キュー（`mail_outbox` / `work_mail_outbox`）の行の有無で判定する。
- push 前に**両システムのフル pytest**を通す（テンプレ／文言変更でもページ HTML 検証テストが落ちうる）。結果件数を明記。

### 5. 仕上げ（ビルド・push・記録）
- 完了後に `docker compose up -d --build`（コード／テンプレート／依存の変更時。**ドキュメントのみの変更では
  build 省略可**）→ 関連ファイルのみを論理単位でコミット → `git push`。コミット／PR に管理番号と項目番号を含める。
  **無関係の作業ツリー変更は混ぜない**。git 書き込みは Bash(git) ツールで実行する。
- 本番反映は別途のため、結果に **「本番未反映」** を明記し、進行状況を `docs/HANDOFF.md`・memory に記録する。

### 完了時セルフチェック（結果に必ず出力する）
- [ ] 管理番号を採番し冒頭に明記／継続なら元番号を引き継いだ
- [ ] 実施項目・積み残しを項目番号で明記した
- [ ] デザイン統一性（既存トークン再利用・2システムパリティ・フォーム/ポップアップ/レスポンシブ/迷わない導線）を満たした
- [ ] 複製ペアを同時更新／タッチポイント（API・DB・PDF・CSV・メール・他画面・他システム）を全確認した
- [ ] 影響・連携の結論（「影響なし」または「連携先の改修要否」）を明記した
- [ ] 本番メールを送らず両システムのフル pytest を通した（結果件数を明記）
- [ ] build → push 済み・「本番未反映」明記・HANDOFF/memory 記録済み

## Project Overview

> 以下は **既存システム＝イスト勤怠レポート for 代々木進学会**（旧称: 指導実績報告システム, `backend/`）の説明。新システム **イスト勤怠レポート for EMPS**（旧 業務連絡表システム, `new_backend/`）は `docs/イスト勤怠レポート for EMPS/` を参照。

Japanese home tutoring lesson report management system (家庭教師 指導実績報告システム). Tutors record monthly lesson reports, which go through a multi-stage approval workflow: tutor → parent → admin_receiver → admin_reviewer (reviewer approval is final). admin_master / admin_chief are outside the approval flow (view, PDF, user/assignment management, stale-report close). Built as a FastAPI + PostgreSQL full-stack web app with server-rendered Jinja2 templates.

## Development Commands

All commands run inside Docker. The backend container handles migrations on startup.

```powershell
# First-time setup
cp .env.example .env
docker compose up -d --build
docker compose exec backend python -m app.scripts.seed   # load demo data

# Daily development
docker compose up -d          # start
docker compose down           # stop
docker compose logs backend -f  # tail logs

# Reset dev environment
docker compose exec backend python -m app.scripts.dev_reset
# Full reset (wipes DB volume):
docker compose down -v && docker compose up -d --build && docker compose exec backend python -m app.scripts.seed
```

**URLs**: App → http://localhost:8000 | API docs → http://localhost:8000/docs | MailHog → http://localhost:8025

**Demo accounts** (all use password `Passw0rd!`): `tutor1@example.com`, `receiver1@example.com`, `reviewer1@example.com`, `master1@example.com`

### Testing

```powershell
docker compose exec backend pytest                     # all tests
docker compose exec backend pytest tests/test_workflow.py  # single file
docker compose exec backend pytest -k "test_submit"   # by name
```

Tests use in-memory SQLite via fixtures in `backend/tests/conftest.py` — no Docker DB needed for test isolation.

### Database Migrations

```powershell
docker compose exec backend alembic upgrade head      # apply all
docker compose exec backend alembic current           # current revision
docker compose exec backend alembic downgrade -1      # rollback one
# Create new migration:
docker compose exec backend alembic revision --autogenerate -m "description"
```

Migration files live in `backend/alembic/versions/` (0001–0019 currently).

## Architecture

### Stack
- **Backend**: FastAPI 0.115 + Python 3.11, SQLAlchemy ORM (psycopg v3 driver)
- **Frontend**: Jinja2 templates + Tailwind CSS (Play CDN) + vanilla JS
- **Auth**: JWT in httpOnly cookie (`access_token`)
- **Email**: aiosmtplib + MailHog (dev), APScheduler for daily reminders at 09:00 JST
- **Export**: openpyxl (Excel), ReportLab (PDF)

### Directory Layout

```
backend/app/
├── api/          # FastAPI routers: auth, users, invitations, assignments,
│                 #   reports, monthly_reports, workflow, stale, chat, pages (HTML views)
├── models/
│   └── entities.py   # All 11 SQLAlchemy ORM models in one file
├── schemas/      # Pydantic request/response schemas per domain
├── services/     # Business logic: workflow_service, notification_service,
│                 #   reminder_service, user_sync_service
├── core/
│   ├── security.py   # JWT creation/validation, bcrypt hashing
│   └── rbac.py       # Role-check decorators
├── templates/    # Jinja2 HTML (tutor/, parent/, admin/, email/ subdirs)
├── static/       # JS and CSS assets
├── scripts/      # seed.py and dev_reset.py (dev-only utilities)
├── config.py     # Pydantic Settings reading from .env
├── database.py   # SQLAlchemy engine + SessionLocal
├── deps.py       # FastAPI dependency injection (get_db, get_current_user)
└── main.py       # App entry point: router registration, scheduler startup
```

### Report Status Flow

```
draft
  → awaiting_parent_approval   (tutor submits)
    → parent_approved          (parent approves)
      → submitted_to_admin     (auto or manual)
        → received             (admin_receiver)
          → admin_approved     (admin_reviewer re-review = final approval)

admin_approved → returned_to_receiver  (reviewer can return after completion)
Any step → returned_to_tutor  (with mandatory comment → email → chat message)
  → resubmit → awaiting_parent_approval...
```

The `re_reviewed` status remains only for legacy in-flight reports (pre-flow-change); reviewers can finalize them via re-review. The old `admin_approve` / `return_from_master` actions are retired (kept in `ReportAction` enum for historical events).

Status transitions are enforced in `backend/app/services/workflow_service.py`. All transitions are audit-logged in the `report_events` table.

### User Roles (6 total)

| Role | Access |
|------|--------|
| `tutor` | Create/submit reports for their own assignments |
| `parent` | Approve/return reports for their children |
| `admin_receiver` | Receive submitted reports; user/assignment management |
| `admin_reviewer` | Re-review received reports (= final approval); user/assignment management |
| `admin_master` | Outside approval flow: view/PDF, user/assignment management |
| `admin_chief` | Same as admin_master + chief-only settings (skip approval, chief invite/ops) |

RBAC is enforced in `backend/app/core/rbac.py` with decorators checked in each API route. Tutors can only access their own assignments; parents see only their children's reports.

### Key Database Tables

- **assignments** — Links tutor + parent + student; holds `skip_parent_approval` flag and reminder config (`reminder_enabled`, `reminder_days_after`, `reminder_count`)
- **lesson_reports** — Core entity; `target_month` (YYYY-MM), status, 6 approval timestamp columns
- **monthly_reports** — 指導月報 (one per assignment × month); tutor fills before submit-to-parent (grade + issues required), parent fills `parent_note` at approval; PDF via `/api/reports/export-monthly`
- **report_events** — Immutable audit log for all status transitions
- **invitations** — 72-hour sign-up tokens (tutor_no pre-assigned for tutors)
- **notifications** — Email delivery log

### Email Notifications

11+ notification types triggered by workflow transitions. Templates in `backend/app/templates/email/`. Sending logic in `notification_service.py`. Monthly reminders fire via APScheduler when within 3 days of month-end.

### Export Format

Excel/CSV download per assignment per month. File naming: `指導実績_{student}_{YYYY年MM月}.xlsx`. Columns: 番号, 日付, 開始, 終了, 休憩, 時間, 科目, 指導内容, ステータス.

## Environment Variables

Key variables from `.env` (see `.env.example` for full list):

| Variable | Default (dev) | Notes |
|----------|--------------|-------|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@db:5432/tutor` | psycopg v3 format |
| `JWT_SECRET` | `change-me-in-production` | Must change in prod |
| `AUTO_CREATE_TABLES` | `false` | Use Alembic migrations instead |
| `ENVIRONMENT` | `development` | Controls dev-only features |
| `TIMEZONE` | `Asia/Tokyo` | Used by scheduler |
| `REMINDER_DAYS_BEFORE_MONTH_END` | `3` | Reminder trigger window |
