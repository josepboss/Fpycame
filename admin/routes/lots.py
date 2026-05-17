import re
import subprocess
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import configparser
import os

router = APIRouter()
templates = Jinja2Templates(directory="admin/templates")

API_DELIVERY_CFG = "configs/api_delivery.cfg"
AUTO_DELIVERY_CFG = "configs/auto_delivery.cfg"


def get_api_cfg():
    """Read api_delivery.cfg using standard ConfigParser."""
    cfg = configparser.ConfigParser()
    cfg.read(API_DELIVERY_CFG, encoding="utf-8")
    return cfg


def get_auto_cfg():
    """Read auto_delivery.cfg using the same settings as Cardinal."""
    cfg = configparser.ConfigParser(delimiters=(":",), interpolation=None)
    cfg.optionxform = str
    cfg.read(AUTO_DELIVERY_CFG, encoding="utf-8")
    return cfg


def write_api_cfg(cfg):
    """Write api_delivery.cfg."""
    with open(API_DELIVERY_CFG, "w", encoding="utf-8") as f:
        cfg.write(f)


def write_auto_cfg(cfg):
    """Write auto_delivery.cfg using configparser (Cardinal format)."""
    with open(AUTO_DELIVERY_CFG, "w", encoding="utf-8") as f:
        cfg.write(f)


def restart_cardinal():
    """Try to restart Cardinal via pm2. Returns True if successful."""
    try:
        result = subprocess.run(
            ["pm2", "restart", "funpay-cardinal"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@router.get("/")
async def list_lots(request: Request):
    api_cfg = get_api_cfg()
    lots_list = []
    for section in api_cfg.sections():
        lots_list.append({
            "name": section,
            "provider": api_cfg[section].get("provider", ""),
            "id": api_cfg[section].get("product_id") or api_cfg[section].get("service_id", ""),
            "enabled": api_cfg[section].getboolean("enabled", fallback=False),
        })
    return templates.TemplateResponse(request, "lots.html", {"lots": lots_list})


@router.post("/save")
async def save_lot(
    lot_name: str = Form(...),
    provider: str = Form(...),
    item_id: str = Form(...),
    enabled: bool = Form(False)
):
    lot_name = lot_name.strip()

    # --- update api_delivery.cfg ---
    api_cfg = get_api_cfg()
    if lot_name not in api_cfg:
        api_cfg.add_section(lot_name)

    api_cfg[lot_name]["provider"] = provider
    if provider == "hstore":
        api_cfg[lot_name]["product_id"] = item_id
        # remove service_id if it exists from previous smmcost assignment
        if "service_id" in api_cfg[lot_name]:
            del api_cfg[lot_name]["service_id"]
    else:
        api_cfg[lot_name]["service_id"] = item_id
        if "product_id" in api_cfg[lot_name]:
            del api_cfg[lot_name]["product_id"]
    api_cfg[lot_name]["enabled"] = "1" if enabled else "0"
    write_api_cfg(api_cfg)

    # --- update auto_delivery.cfg ---
    auto_cfg = get_auto_cfg()
    if lot_name not in auto_cfg:
        auto_cfg.add_section(lot_name)

    # Only write the response key (as Cardinal's Telegram bot does)
    auto_cfg[lot_name]["response"] = "Спасибо за покупку, $username!\n\t\n\tВот твой товар:\n\t\n\t$product"
    write_auto_cfg(auto_cfg)

    # --- restart Cardinal via pm2 ---
    restart_ok = restart_cardinal()
    if restart_ok:
        return RedirectResponse(url="/lots", status_code=303)
    else:
        # Show a warning but still redirect
        return JSONResponse(
            content={
                "message": "Saved but Cardinal restart failed. Please restart manually from the Status page.",
                "redirect": "/lots"
            },
            status_code=200
        )


@router.get("/delete/{lot_name}")
async def delete_lot(lot_name: str):
    # Remove from api_delivery.cfg
    api_cfg = get_api_cfg()
    if lot_name in api_cfg:
        api_cfg.remove_section(lot_name)
    write_api_cfg(api_cfg)

    # Remove from auto_delivery.cfg
    auto_cfg = get_auto_cfg()
    if lot_name in auto_cfg:
        auto_cfg.remove_section(lot_name)
    write_auto_cfg(auto_cfg)

    # Restart Cardinal
    restart_ok = restart_cardinal()
    if restart_ok:
        return RedirectResponse(url="/lots", status_code=303)
    else:
        return JSONResponse(
            content={
                "message": "Deleted but Cardinal restart failed. Please restart manually from the Status page.",
                "redirect": "/lots"
            },
            status_code=200
        )