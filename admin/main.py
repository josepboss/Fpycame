import os
from fastapi import FastAPI, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from admin.auth import get_current_user
from admin.routes import lots, settings, logs, status

app = FastAPI(title="FunPay Cardinal Admin", version="1.0.0")
templates = Jinja2Templates(directory="admin/templates")

# Mount static files (if any)
if not os.path.exists("admin/static"):
    os.makedirs("admin/static")
app.mount("/static", StaticFiles(directory="admin/static"), name="static")

# Include routers
app.include_router(lots.router, prefix="/lots", tags=["lots"], dependencies=[Depends(get_current_user)])
app.include_router(settings.router, prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])
app.include_router(logs.router, prefix="/logs", tags=["logs"], dependencies=[Depends(get_current_user)])
app.include_router(status.router, prefix="/status", tags=["status"], dependencies=[Depends(get_current_user)])


@app.get("/")
async def root(request: Request, user: str = Depends(get_current_user)):
    return templates.TemplateResponse(request, "base.html", {"user": user})