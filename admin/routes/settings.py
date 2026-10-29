from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from admin.templating import templates
import configparser
from admin.auth import get_csrf_token, validate_csrf_token, translate
from Utils import discord_confirmation

router = APIRouter()

MAIN_CFG = "configs/_main.cfg"
API_KEYS_CFG = "configs/api_keys.cfg"
ADMIN_CFG = "configs/admin.cfg"


@router.get("/")
async def show_settings(request: Request):
    main_cfg = configparser.ConfigParser(interpolation=None)
    main_cfg.read(MAIN_CFG, encoding="utf-8")
    api_cfg = configparser.ConfigParser(interpolation=None)
    api_cfg.read(API_KEYS_CFG, encoding="utf-8")
    discord = discord_confirmation.settings()
    with discord_confirmation.database() as db:
        reminder_states = dict(db.execute('SELECT state,COUNT(*) FROM reminders GROUP BY state').fetchall())

    return templates.TemplateResponse(request=request, name="settings.html", context={
        "request": request,
        "discord": {key: value for key,value in discord.items() if key != 'token'},
        "discord_token_saved": bool(discord['token']),
        "discord_states": reminder_states,
        "csrf_token": get_csrf_token(), "_": translate,
        "golden_key": main_cfg.get("FunPay", "golden_key", fallback=""),
        "hstore_key": api_cfg["hstore"].get("api_key", "") if "hstore" in api_cfg else "",
        "hstore_secret": api_cfg["hstore"].get("api_secret", "") if "hstore" in api_cfg else "",
        "smmcost_key": api_cfg["smmcost"].get("api_key", "") if "smmcost" in api_cfg else "",
        "dark_key": api_cfg["dark"].get("api_key", "") if "dark" in api_cfg else "",
        "djekxa_key": api_cfg["djekxa"].get("api_key", "") if "djekxa" in api_cfg else "",
    })


@router.post('/save_discord')
def save_discord(csrf_token: str = Form(''), channel_id: str = Form(''),
                 token: str = Form(''), enabled: bool = Form(False)) -> RedirectResponse:
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    try:
        discord_confirmation.save_settings(enabled,channel_id,token)
    except ValueError:
        return RedirectResponse('/settings/?discord_error=true',status_code=303)
    return RedirectResponse('/settings/?discord_saved=true',status_code=303)


@router.post("/save_funpay")
async def save_funpay(golden_key: str = Form(...)):
    config = configparser.ConfigParser(interpolation=None)
    config.read(MAIN_CFG, encoding="utf-8")
    config["FunPay"]["golden_key"] = golden_key
    with open(MAIN_CFG, "w", encoding="utf-8") as f:
        config.write(f)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/save_api")
async def save_api(request: Request) -> RedirectResponse:
    # Empty password inputs mean "keep existing", including partial submissions.
    form = await request.form()
    config = configparser.ConfigParser(interpolation=None)
    config.read(API_KEYS_CFG, encoding="utf-8")
    from admin.routes.providers import _load_json, _save_json, PROVIDERS_CFG
    providers = _load_json(PROVIDERS_CFG)
    for section in ('hstore', 'smmcost', 'dark', 'djekxa'):
        fields = [('api_key', f'{section}_key')]
        if section == 'hstore':
            fields.append(('api_secret', 'hstore_secret'))
        for key, name in fields:
            value = str(form.get(name, '')).strip()
            if not value:
                continue
            if section not in config:
                config.add_section(section)
            config[section][key] = value
            provider = providers.setdefault(section, {})
            credentials = provider.get('credentials', [])
            first = dict(credentials[0]) if credentials else {}
            first[key] = value
            provider['credentials'] = [first] + credentials[1:]
    with open(API_KEYS_CFG, 'w', encoding='utf-8') as stream:
        config.write(stream)
    _save_json(PROVIDERS_CFG, providers)
    return RedirectResponse(url='/settings', status_code=303)


@router.post("/save_admin")
async def save_admin(username: str = Form(...), password: str = Form(...)):
    config = configparser.ConfigParser(interpolation=None)
    config.read(ADMIN_CFG, encoding="utf-8")
    if "admin" not in config:
        config.add_section("admin")
    config["admin"]["username"] = username
    config["admin"]["password"] = password
    with open(ADMIN_CFG, "w", encoding="utf-8") as f:
        config.write(f)
    return RedirectResponse(url="/settings", status_code=303)
