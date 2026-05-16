import re
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import configparser
import os

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

API_DELIVERY_CFG = "configs/api_delivery.cfg"
AUTO_DELIVERY_CFG = "configs/auto_delivery.cfg"


def get_cfgs():
    api_cfg = configparser.ConfigParser()
    api_cfg.read(API_DELIVERY_CFG, encoding="utf-8")
    auto_cfg = configparser.ConfigParser()
    auto_cfg.read(AUTO_DELIVERY_CFG, encoding="utf-8")
    return api_cfg, auto_cfg


def save_cfgs(api_cfg, auto_cfg):
    with open(API_DELIVERY_CFG, "w", encoding="utf-8") as f:
        api_cfg.write(f)
    with open(AUTO_DELIVERY_CFG, "w", encoding="utf-8") as f:
        auto_cfg.write(f)


def sanitize_filename(name: str) -> str:
    """Sanitize lot name to a valid filename."""
    sanitized = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
    return f"{sanitized}.txt"


@router.get("/")
async def list_lots(request: Request):
    api_cfg, _ = get_cfgs()
    lots_list = []
    for section in api_cfg.sections():
        lots_list.append({
            "name": section,
            "provider": api_cfg[section].get("provider", ""),
            "id": api_cfg[section].get("product_id") or api_cfg[section].get("service_id", ""),
            "enabled": api_cfg[section].getboolean("enabled", fallback=False),
        })
    return templates.TemplateResponse("lots.html", {"request": request, "lots": lots_list})


@router.post("/save")
async def save_lot(
        lot_name: str = Form(...),
        provider: str = Form(...),
        item_id: str = Form(...),
        enabled: bool = Form(False)
):
    api_cfg, auto_cfg = get_cfgs()

    if lot_name not in api_cfg:
        api_cfg.add_section(lot_name)

    api_cfg[lot_name]["provider"] = provider
    if provider == "hstore":
        api_cfg[lot_name]["product_id"] = item_id
        api_cfg[lot_name].pop("service_id", None)
    else:
        api_cfg[lot_name]["service_id"] = item_id
        api_cfg[lot_name].pop("product_id", None)
    api_cfg[lot_name]["enabled"] = "1" if enabled else "0"

    # Update auto_delivery.cfg
    if lot_name not in auto_cfg:
        auto_cfg.add_section(lot_name)
    auto_cfg[lot_name]["response"] = "$product"
    auto_cfg[lot_name]["productsFileName"] = sanitize_filename(lot_name)

    save_cfgs(api_cfg, auto_cfg)
    return RedirectResponse(url="/lots", status_code=303)


@router.get("/delete/{lot_name}")
async def delete_lot(lot_name: str):
    api_cfg, auto_cfg = get_cfgs()
    if lot_name in api_cfg:
        api_cfg.remove_section(lot_name)
    if lot_name in auto_cfg:
        auto_cfg.remove_section(lot_name)
    save_cfgs(api_cfg, auto_cfg)
    return RedirectResponse(url="/lots", status_code=303)