from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import configparser

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

MAIN_CFG = "configs/_main.cfg"
API_KEYS_CFG = "configs/api_keys.cfg"
ADMIN_CFG = "configs/admin.cfg"


@router.get("/")
async def show_settings(request: Request):
    main_cfg = configparser.ConfigParser()
    main_cfg.read(MAIN_CFG, encoding="utf-8")
    api_cfg = configparser.ConfigParser()
    api_cfg.read(API_KEYS_CFG, encoding="utf-8")

    return templates.TemplateResponse("settings.html", request, {
        "golden_key": main_cfg["FunPay"].get("golden_key", ""),
        "hstore_key": api_cfg["hstore"].get("api_key", "") if "hstore" in api_cfg else "",
        "hstore_secret": api_cfg["hstore"].get("api_secret", "") if "hstore" in api_cfg else "",
        "smmcost_key": api_cfg["smmcost"].get("api_key", "") if "smmcost" in api_cfg else "",
    })


@router.post("/save_funpay")
async def save_funpay(golden_key: str = Form(...)):
    config = configparser.ConfigParser()
    config.read(MAIN_CFG, encoding="utf-8")
    config["FunPay"]["golden_key"] = golden_key
    with open(MAIN_CFG, "w", encoding="utf-8") as f:
        config.write(f)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/save_api")
async def save_api(
        hstore_key: str = Form(""),
        hstore_secret: str = Form(""),
        smmcost_key: str = Form("")
):
    config = configparser.ConfigParser()
    config.read(API_KEYS_CFG, encoding="utf-8")
    if "hstore" not in config:
        config.add_section("hstore")
    if "smmcost" not in config:
        config.add_section("smmcost")
    config["hstore"]["api_key"] = hstore_key
    config["hstore"]["api_secret"] = hstore_secret
    config["smmcost"]["api_key"] = smmcost_key
    with open(API_KEYS_CFG, "w", encoding="utf-8") as f:
        config.write(f)
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/save_admin")
async def save_admin(username: str = Form(...), password: str = Form(...)):
    config = configparser.ConfigParser()
    config.read(ADMIN_CFG, encoding="utf-8")
    if "admin" not in config:
        config.add_section("admin")
    config["admin"]["username"] = username
    config["admin"]["password"] = password
    with open(ADMIN_CFG, "w", encoding="utf-8") as f:
        config.write(f)
    return RedirectResponse(url="/settings", status_code=303)