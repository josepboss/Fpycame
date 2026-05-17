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
    auto_cfg = configparser.ConfigParser(delimiters=(":",), interpolation=None)
    auto_cfg.optionxform = str
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


def write_auto_delivery_cfg(auto_cfg):
    """Write auto_delivery.cfg in the exact format Cardinal expects."""
    with open(AUTO_DELIVERY_CFG, "w", encoding="utf-8") as f:
        for section in auto_cfg.sections():
            f.write(f"[{section}]\n")
            for key, value in auto_cfg.items(section):
                if key == "response":
                    f.write(f"response : {value}\n")
                else:
                    f.write(f"{key} : {value}\n")
            f.write("\n")


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
    return templates.TemplateResponse(request, "lots.html", {"lots": lots_list})


@router.post("/save")
async def save_lot(
        lot_name: str = Form(...),
        provider: str = Form(...),
        item_id: str = Form(...),
        enabled: bool = Form(False)
):
    api_cfg, auto_cfg = get_cfgs()
    lot_name = lot_name.strip()

    # Save to api_delivery.cfg
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

    # Save to auto_delivery.cfg in Cardinal's exact format
    if lot_name not in auto_cfg:
        auto_cfg[lot_name] = {}

    # Set the multiline response with tab-indented lines
    response_text = "Спасибо за покупку, $username!\n\t\n\tВот твой товар:\n\t\n\t$product"
    auto_cfg[lot_name]["response"] = response_text

    # Write both configs
    with open(API_DELIVERY_CFG, "w", encoding="utf-8") as f:
        api_cfg.write(f)

    write_auto_delivery_cfg(auto_cfg)

    return RedirectResponse(url="/lots", status_code=303)


@router.get("/delete/{lot_name}")
async def delete_lot(lot_name: str):
    api_cfg, auto_cfg = get_cfgs()
    if lot_name in api_cfg:
        api_cfg.remove_section(lot_name)
    if lot_name in auto_cfg:
        auto_cfg.remove_section(lot_name)

    # Write both configs
    with open(API_DELIVERY_CFG, "w", encoding="utf-8") as f:
        api_cfg.write(f)

    write_auto_delivery_cfg(auto_cfg)

    return RedirectResponse(url="/lots", status_code=303)