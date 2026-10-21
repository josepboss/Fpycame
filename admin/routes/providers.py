import json
import os
import configparser
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from admin.templating import templates

router = APIRouter()

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


def _get_providers_data():
    """
    Build the full dashboard data from providers_cfg.json (the source of truth).
    For backward compatibility, also reads api_keys.cfg if it exists,
    but providers_cfg.json credentials take precedence.
    """
    pcfg = _load_json(PROVIDERS_CFG)
    hcache = _load_json(HEALTH_CACHE)

    # Also read api_keys.cfg for backward compatibility
    api_cfg = configparser.ConfigParser(interpolation=None)
    api_cfg.read(API_KEYS_CFG, encoding="utf-8")

    providers = {}
    for name in ["hstore", "smmcost", "dark", "djekxa"]:
        pc = pcfg.get(name, {})
        hc = hcache.get(name, {})

        # Extract credentials from providers_cfg.json
        creds_list = pc.get("credentials", [])
        creds = creds_list[0] if creds_list else {}

        # For backward compatibility, also check api_keys.cfg
        if not creds.get("api_key") and api_cfg.has_section(name):
            creds["api_key"] = api_cfg[name].get("api_key", "")
        if not creds.get("api_secret") and api_cfg.has_section(name):
            creds["api_secret"] = api_cfg[name].get("api_secret", "")

        providers[name] = {
            "domain": pc.get("active_domain", f"{name}.com"),
            "domains": pc.get("domains", []),
            "rate_limit": pc.get("rate_limit", 5),
            "auth_type": pc.get("auth_type", "key"),
            "has_credentials": bool(creds),
            "is_online": hc.get("is_online", False),
            "last_success_time": hc.get("last_success_time"),
            "last_error_time": hc.get("last_error_time"),
            "last_error_message": hc.get("last_error_message"),
            "failed_order_count": hc.get("failed_order_count", 0),
            "successful_order_count": hc.get("successful_order_count", 0),
            "consecutive_failures": hc.get("consecutive_failures", 0),
            "api_key": creds.get("api_key", ""),
            "api_secret": creds.get("api_secret", ""),
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
    return templates.TemplateResponse(request=request, name="providers.html", context={"request": request, **data})


@router.post("/save_api_keys")
async def save_api_keys(request: Request) -> RedirectResponse:
    # Each provider card submits only its own fields. Preserve every omitted key.
    form = await request.form()
    cfg = _load_json(PROVIDERS_CFG)
    api_cfg = configparser.ConfigParser(interpolation=None)
    api_cfg.read(API_KEYS_CFG, encoding='utf-8')
    for name in ('hstore', 'dark', 'djekxa', 'smmcost'):
        fields = [('api_key', f'{name}_key')]
        if name == 'hstore':
            fields.append(('api_secret', 'hstore_secret'))
        if not any(field in form for _, field in fields):
            continue
        pc = cfg.setdefault(name, {})
        credentials = pc.get('credentials', [])
        creds = dict(credentials[0]) if credentials else {}
        if name not in api_cfg:
            api_cfg.add_section(name)
        for key, field in fields:
            if field in form:
                creds[key] = str(form[field]).strip()
                api_cfg[name][key] = creds[key]
        pc['credentials'] = [creds] + credentials[1:]
    _save_json(PROVIDERS_CFG, cfg)
    with open(API_KEYS_CFG, 'w', encoding='utf-8') as stream:
        api_cfg.write(stream)
    return RedirectResponse(url='/providers', status_code=303)


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
    try:
        from cardinal import Cardinal
        crd = Cardinal()
        # Import and run health check
        from plugins.providers.health import HealthMonitor
        hm = HealthMonitor(crd)
        hm.run_checks()
        return JSONResponse({"status": "done", "message": "Health checks completed."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.post("/run_offer_check")
async def run_offer_check():
    try:
        from admin.services.offer_import import make_account
        from admin.services.offer_stock import check_stock, mappings
        account = make_account()
        check_stock(account)
        return JSONResponse({"status": "done", "changes": len(mappings(account.id))})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)