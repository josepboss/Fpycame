from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

LOG_FILE = "logs/log.log"


@router.get("/")
async def show_logs(request: Request):
    return templates.TemplateResponse("logs.html", {"request": request})


@router.get("/stream")
async def stream_logs():
    if not os.path.exists(LOG_FILE):
        return PlainTextResponse("Log file not found.")
    with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
        return PlainTextResponse("".join(lines[-200:]))


@router.post("/clear")
async def clear_logs():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")
    return PlainTextResponse("Logs cleared.")