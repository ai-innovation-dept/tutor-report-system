# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

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

Migration files live in `backend/alembic/versions/` (0001–0011 currently).

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
├── api/          # 7 FastAPI routers: auth, users, invitations, assignments,
│                 #   reports, workflow, chat, pages (HTML views)
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
