import re
import subprocess
from urllib.parse import unquote, quote
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import configparser
import os

from locales.localizer import Localizer
from admin.services.delivery_mapping import save_delivery_mapping

from admin.auth import get_csrf_token, validate_csrf_token

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
async def list_lots(request: Request, flash: str = ""):
    api_cfg = get_api_cfg()
    lots_list = []
    for section in api_cfg.sections():
        lots_list.append({
            "name": section,
            "provider": api_cfg[section].get("provider", ""),
            "id": api_cfg[section].get("product_id") or api_cfg[section].get("service_id", ""),
            "enabled": api_cfg[section].getboolean("enabled", fallback=False),
        })
    csrf_token = get_csrf_token()
    return templates.TemplateResponse(request=request, name="lots.html", context={
        "request": request,
        "lots": lots_list,
        "import_label": Localizer().translate("oi_heading", language="en"),
        "flash": flash,
        "csrf_token": csrf_token,
    })


@router.post("/save")
async def save_lot(
    lot_name: str = Form(...),
    provider: str = Form(...),
    item_id: str = Form(...),
    enabled: bool = Form(False),
    csrf_token: str = Form(""),
):
    if not csrf_token or not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    lot_name = lot_name.strip()

    save_delivery_mapping(lot_name, provider, item_id, enabled)

    restart_ok = restart_cardinal()
    if restart_ok:
        return RedirectResponse(url="/lots?flash=Lot+saved+successfully", status_code=303)
    else:
        return JSONResponse(
            content={
                "message": "Saved but Cardinal restart failed. Please restart manually from the Status page.",
                "redirect": "/lots"
            },
            status_code=200
        )


@router.post("/delete")
async def delete_lot(
    lot_name: str = Form(...),
    csrf_token: str = Form(""),
):
    if not csrf_token or not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    lot_name = lot_name.strip()
    stock_file_name = None

    # 1. Remove from api_delivery.cfg
    api_cfg = get_api_cfg()
    if lot_name in api_cfg:
        api_cfg.remove_section(lot_name)
    write_api_cfg(api_cfg)

    # 2. Remove from auto_delivery.cfg
    auto_cfg = get_auto_cfg()
    if lot_name in auto_cfg:
        stock_file_name = auto_cfg[lot_name].get("productsFileName")
        auto_cfg.remove_section(lot_name)
    write_auto_cfg(auto_cfg)

    # 3. Delete the stock file
    if stock_file_name:
        stock_path = os.path.join("storage/products", stock_file_name)
        if os.path.exists(stock_path):
            try:
                os.remove(stock_path)
            except OSError:
                pass

    # 4. Restart Cardinal
    restart_cardinal()

    # 5. Redirect with flash
    flash_msg = quote(f'Offer mapping for "{lot_name}" deleted successfully')
    return RedirectResponse(url=f"/lots?flash={flash_msg}", status_code=303)