from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import subprocess
import time
import json

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")


def get_pm2_status():
    """Get pm2 process info for funpay-cardinal."""
    try:
        result = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=5)
        processes = json.loads(result.stdout)
        for proc in processes:
            if proc["name"] == "funpay-cardinal":
                status = proc["pm2_env"]["status"]
                uptime = proc["pm2_env"].get("pm_uptime", 0)
                return status, uptime
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return "unknown", 0


@router.get("/")
async def show_status(request: Request):
    status, uptime = get_pm2_status()
    uptime_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(uptime / 1000)) if uptime else "N/A"
    return templates.TemplateResponse("status.html", request, {"status": status, "uptime": uptime_str})


@router.post("/action/{cmd}")
async def cardinal_action(cmd: str):
    if cmd in ["restart", "start", "stop"]:
        try:
            subprocess.run(["pm2", cmd, "funpay-cardinal"], capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            pass
    return RedirectResponse(url="/status", status_code=303)