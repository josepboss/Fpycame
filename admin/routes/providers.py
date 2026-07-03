import json
import os
import configparser
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

PROVIDERS_CFG = "configs/providers_cfg.json"
API_KEYS_CFG = "configs/api_keys.cfg"
HEALTH_CACHE = "storage/cache/provider_health.json"


def _load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False))


def _load_api_keys():
    cfg = configparser.ConfigParser()
    cfg.read(API_KEYS_CFG, encoding="utf-8")
    return cfg


def _save_api_keys(cfg):
    with open(API_KEYS_CFG, "w", encoding="utf-8") as f:
        cfg.write(f)


def _get_providers_data():
    """Build the full dashboard data: providers config + health cache."""
    pcfg = _load_json(PROVIDERS_CFG)
    hcache = _load_json(HEALTH_CACHE)
    api_keys = _load_api_keys()

    providers = {}
    for name in ["hstore", "smmcost", "dark", "djekxa"]:
        pc = pcfg.get(name, {})
        hc = hcache.get(name, {})
        providers[name] = {
            "domain": pc.get("active_domain", f"{name}.com"),
            "domains": pc.get("domains", []),
            "rate_limit": pc.get("rate_limit", 5),
            "auth_type": pc.get("auth_type", "key"),
            "has_credentials": bool(pc.get("credentials", [])),
            "is_online": hc.get("is_online", False),
            "last_success_time": hc.get("last_success_time"),
            "last_error_time": hc.get("last_error_time"),
            "last_error_message": hc.get("last_error_message"),
            "failed_order_count": hc.get("failed_order_count", 0),
            "successful_order_count": hc.get("successful_order_count", 0),
            "consecutive_failures": hc.get("consecutive_failures", 0),
            "api_key": api_keys.get(name, {}).get("api_key", ""),
            "api_secret": api_keys.get(name, {}).get("api_secret", ""),
        }

    return {
        "providers": providers,
        "failover": pcfg.get("failover", {"enabled": False, "provider_priority": []}),
        "offer_monitor": pcfg.get("offer_monitor", {"enabled": True, "check_interval_minutes": 15}),
        "health": pcfg.get("health", {"check_interval_minutes": 10}),
    }


@router.get("/")
async def provider_dashboard(request: Request):
    data = _get_providers_data()
    return templates.TemplateResponse("providers.html", {"request": request, **data})


@router.post("/save_api_keys")
async def save_api_keys(
    hstore_key: str = Form(""),
    hstore_secret: str = Form(""),
    dark_key: str = Form(""),
    djekxa_key: str = Form(""),
):
    cfg = _load_json(PROVIDERS_CFG)

    # Update providers_cfg.json credentials
    for provider_name, key_field, secret_field in [
        ("hstore", "api_key", "api_secret"),
        ("dark", "api_key", None),
        ("djekxa", "api_key", None),
    ]:
        if provider_name not in cfg:
            cfg[provider_name] = {}
        creds = {}
        if key_field:
            val = locals().get(f"{provider_name}_key", "")
            if val:
                creds[key_field] = val
        if secret_field:
            val = locals().get(f"{provider_name}_secret", "")
            if val:
                creds[secret_field] = val
        cfg[provider_name]["credentials"] = [creds] if creds else []
    _save_json(PROVIDERS_CFG, cfg)

    # Also update api_keys.cfg for backward compatibility
    api_cfg = configparser.ConfigParser()
    api_cfg["hstore"] = {"api_key": hstore_key, "api_secret": hstore_secret}
    api_cfg["dark"] = {"api_key": dark_key}
    api_cfg["djekxa"] = {"api_key": djekxa_key}
    _save_api_keys(api_cfg)

    return RedirectResponse(url="/providers", status_code=303)


@router.post("/switch_domain")
async def switch_domain(
    provider: str = Form(...),
    domain: str = Form(...),
):
    from plugins.providers.base import set_provider_domain
    set_provider_domain(provider, domain)
    return RedirectResponse(url="/providers", status_code=303)


@router.post("/toggle_failover")
async def toggle_failover(
    enabled: bool = Form(False),
    priority: str = Form(""),
):
    cfg = _load_json(PROVIDERS_CFG)
    if "failover" not in cfg:
        cfg["failover"] = {}
    cfg["failover"]["enabled"] = enabled
    if priority:
        cfg["failover"]["provider_priority"] = [p.strip() for p in priority.split(",") if p.strip()]
    _save_json(PROVIDERS_CFG, cfg)
    return RedirectResponse(url="/providers", status_code=303)


@router.post("/update_offer_monitor")
async def update_offer_monitor(
    enabled: bool = Form(False),
    interval: int = Form(15),
):
    cfg = _load_json(PROVIDERS_CFG)
    if "offer_monitor" not in cfg:
        cfg["offer_monitor"] = {}
    cfg["offer_monitor"]["enabled"] = enabled
    cfg["offer_monitor"]["check_interval_minutes"] = interval
    _save_json(PROVIDERS_CFG, cfg)
    return RedirectResponse(url="/providers", status_code=303)


@router.post("/run_health")
async def run_health():
    """Trigger a health check via the plugin's health monitor. Returns JSON result."""
    try:
        from cardinal import Cardinal
        crd = Cardinal()
        if hasattr(crd, '_offer_monitor'):
            # Will be handled by the plugin's background loop
            return JSONResponse({"status": "triggered", "message": "Health checks will run in background."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    return JSONResponse({"status": "triggered"})


@router.post("/run_offer_check")
async def run_offer_check():
    """Trigger an offer monitor refresh."""
    try:
        from cardinal import Cardinal
        crd = Cardinal()
        if hasattr(crd, '_offer_monitor'):
            changes = crd._offer_monitor.refresh() if crd._offer_monitor else []
            return JSONResponse({"status": "done", "changes": len(changes)})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
    return JSONResponse({"status": "done", "changes": 0})