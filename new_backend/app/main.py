from fastapi import FastAPI

from app.api import auth, reports, users, admin, pages, invitations, assignments, chat
from app.services.reminder_service import start_scheduler

app = FastAPI(title="Work Report System", version="0.1.0")

app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(reports.stale_router)
app.include_router(users.router)
app.include_router(admin.router)
app.include_router(invitations.router)
app.include_router(assignments.router)
app.include_router(chat.router)


@app.on_event("startup")
def on_startup() -> None:
    start_scheduler()


@app.get("/health")
def health():
    return {"status": "ok", "system": "new"}
