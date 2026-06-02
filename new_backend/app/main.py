from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import auth, reports, users, admin

app = FastAPI(title="Work Report System", version="0.1.0")

app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(users.router)
app.include_router(admin.router)


@app.get("/w/health")
def health():
    return {"status": "ok", "system": "new"}
