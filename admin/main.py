import os
from datetime import datetime
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from admin.auth import get_current_user
from admin.routes import lots, settings, logs, status, providers, import_offers

app = FastAPI(title="FunPay Cardinal Admin", version="1.0.0")
templates = Jinja2Templates(directory="admin/templates")

# Register custom Jinja2 filter: timestamp to string
def timestamp_to_str(ts):
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return "N/A"
templates.env.filters["timestamp_to_str"] = timestamp_to_str

# Mount static files (if any)
if not os.path.exists("admin/static"):
    os.makedirs("admin/static")
app.mount("/static", StaticFiles(directory="admin/static"), name="static")

# Include routers
app.include_router(import_offers.router, prefix="/imports", tags=["imports"], dependencies=[Depends(get_current_user)])
app.include_router(lots.router, prefix="/lots", tags=["lots"], dependencies=[Depends(get_current_user)])
app.include_router(settings.router, prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])
app.include_router(logs.router, prefix="/logs", tags=["logs"], dependencies=[Depends(get_current_user)])
app.include_router(status.router, prefix="/status", tags=["status"], dependencies=[Depends(get_current_user)])
app.include_router(providers.router, prefix="/providers", tags=["providers"], dependencies=[Depends(get_current_user)])


@app.get("/")
async def root(request: Request, user: str = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="base.html", context={"request": request, "user": user})