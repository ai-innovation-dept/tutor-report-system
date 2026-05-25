# === Phase 0: 基盤・インフラ START ===
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import auth, chat, invitations, pages, reports, users, workflow
from app.config import settings
from app.database import Base, engine
from app.services.reminder_service import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Tutor Report System")

origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(invitations.router)
app.include_router(reports.router)
app.include_router(workflow.router)
app.include_router(chat.router)
app.include_router(pages.router)


@app.on_event("startup")
def on_startup() -> None:
    if settings.auto_create_tables:
        Base.metadata.create_all(bind=engine)
    start_scheduler()


@app.get("/health")
def health():
    return {"status": "ok"}
# === Phase 0 END ===
