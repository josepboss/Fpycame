import os
from fastapi import FastAPI, Request, Depends
from admin.templating import templates
from fastapi.staticfiles import StaticFiles
from admin.auth import get_current_user, translate
from admin.routes import lots, settings, logs, status, providers, import_offers, login

app = FastAPI(title="FunPay Cardinal Admin", version="1.0.0")

@app.middleware("http")
async def protect_admin_requests(request: Request, call_next):
    from fastapi.responses import JSONResponse
    request.state.logout_label = translate("auth_logout")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        if (request.headers.get("sec-fetch-site") == "cross-site" or
                (origin is not None and origin != expected)):
            return JSONResponse({"detail": translate("auth_csrf")}, status_code=403)
    response = await call_next(request)
    if request.url.path != "/static" and not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# Mount static files (if any)
if not os.path.exists("admin/static"):
    os.makedirs("admin/static")
app.mount("/static", StaticFiles(directory="admin/static"), name="static")

# Include routers
app.include_router(login.router)
app.include_router(import_offers.router, prefix="/imports", tags=["imports"], dependencies=[Depends(get_current_user)])
app.include_router(lots.router, prefix="/lots", tags=["lots"], dependencies=[Depends(get_current_user)])
app.include_router(settings.router, prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])
app.include_router(logs.router, prefix="/logs", tags=["logs"], dependencies=[Depends(get_current_user)])
app.include_router(status.router, prefix="/status", tags=["status"], dependencies=[Depends(get_current_user)])
app.include_router(providers.router, prefix="/providers", tags=["providers"], dependencies=[Depends(get_current_user)])


@app.get("/")
async def root(request: Request, user: str = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="base.html", context={"request": request, "user": user})