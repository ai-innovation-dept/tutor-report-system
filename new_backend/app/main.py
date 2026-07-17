from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import auth, reports, users, admin, pages, invitations, assignments, chat, contracts, no_lesson_months
from app.services.reminder_service import start_scheduler

app = FastAPI(title="Work Report System", version="0.1.0")

# 静的アセット配信（ヘルプ用スクリーンショット等）。既存システム(backend)と同じ /static/ で揃える。
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(reports.stale_router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(invitations.router)
app.include_router(assignments.router)
app.include_router(chat.router)
app.include_router(contracts.router)
app.include_router(no_lesson_months.router)


@app.on_event("startup")
def on_startup() -> None:
    start_scheduler()


@app.get("/health")
def health():
    return {"status": "ok", "system": "new"}
