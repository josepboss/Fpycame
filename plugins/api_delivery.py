from __future__ import annotations
import os
import time
import json
import hmac
import hashlib
import re
import logging
import threading
import configparser
import secrets
from typing import TYPE_CHECKING, Dict, Any, Optional

import requests

if TYPE_CHECKING:
    from cardinal import Cardinal
    from FunPayAPI.updater.events import NewOrderEvent, NewMessageEvent


# Plugin Metadata
NAME = "API Delivery"
VERSION = "1.0.0"
DESCRIPTION = "Dynamic order fulfillment via HStore and SMMCost APIs."
CREDITS = "Dyad"
UUID = "7f3b8e2a-9c1d-4b5e-a6f0-d2c3b4a5e6f7"
SETTINGS_PAGE = False
BIND_TO_DELETE = None

logger = logging.getLogger("FPC.api_delivery")
_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}

# Config Paths
API_DELIVERY_CFG = "configs/api_delivery.cfg"
API_KEYS_CFG = "configs/api_keys.cfg"


def init_configs():
    """Auto-create template config files if they don't exist."""
    if not os.path.exists(API_DELIVERY_CFG):
        with open(API_DELIVERY_CFG, "w", encoding="utf-8") as f:
            f.write("# Lot mapping configuration for API Delivery plugin\n")
            f.write("# One section per FunPay lot. Section name must match auto_delivery.cfg exactly.\n")
            f.write("#\n")
            f.write("[Example Lot Name]\n")
            f.write("provider = hstore\n")
            f.write("product_id = 381\n")
            f.write("enabled = 1\n")

    if not os.path.exists(API_KEYS_CFG):
        config = configparser.ConfigParser()
        config["hstore"] = {"api_key": "", "api_secret": ""}
        config["smmcost"] = {"api_key": ""}
        with open(API_KEYS_CFG, "w", encoding="utf-8") as f:
            config.write(f)

    if not os.path.exists("storage/products"):
        os.makedirs("storage/products")


def get_api_keys() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(API_KEYS_CFG, encoding="utf-8")
    return config


def get_delivery_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(API_DELIVERY_CFG, encoding="utf-8")
    return config


# ---------------------------------------------------------------------------
# HStore Provider
# ---------------------------------------------------------------------------

def hstore_request(method: str, path: str, query: str = "", body: Optional[Dict] = None,
                   idempotency_key: Optional[str] = None) -> Optional[requests.Response]:
    """Make a signed HMAC-SHA256 request to HStore."""
    keys = get_api_keys()
    if "hstore" not in keys or not keys["hstore"].get("api_key"):
        logger.error("HStore API key not configured.")
        return None

    api_key = keys["hstore"]["api_key"]
    api_secret = keys["hstore"]["api_secret"]
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    body_json = json.dumps(body, separators=(",", ":")) if body else ""
    body_hash = hashlib.sha256(body_json.encode()).hexdigest() if body_json else ""

    canonical = f"{method.upper()}\n{path}\n{query}\n{timestamp}\n{nonce}\n{body_hash}"
    signature = hmac.new(api_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    headers = {
        "X-API-Key": api_key,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    url = f"https://hstore.site{path}"
    if query:
        url += f"?{query}"
    try:
        if method.upper() == "POST":
            return requests.post(url, headers=headers, data=body_json, timeout=30)
        else:
            return requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        logger.error(f"HStore request failed: {e}")
        return None


def handle_hstore(crd: Cardinal, event: NewOrderEvent) -> bool:
    """Fetch credentials from HStore and write to stock file."""
    lot_cfg = get_delivery_config()
    lot_name = event.config_section_name
    if not lot_name or lot_name not in lot_cfg:
        logger.info(f"[API Delivery] Lot {lot_name} not in api_delivery.cfg, skipping HStore.")
        return False

    product_id = lot_cfg[lot_name].get("product_id")
    if not product_id:
        logger.error(f"[API Delivery] product_id missing for {lot_name}.")
        return False

    order = event.order
    ext_id = f"funpay-{order.id}"
    body = {
        "product_id": int(product_id),
        "quantity": getattr(order, 'amount', 1),
        "external_order_id": ext_id,
    }

    idem_key = f"idem-funpay-{order.id}"
    resp = hstore_request("POST", "/api/v1/orders", body=body, idempotency_key=idem_key)

    if resp and resp.status_code == 409:
        # Duplicate – recover by lookup
        resp = hstore_request("GET", "/api/v1/orders/lookup",
                              query=f"external_order_id={ext_id}")

    if resp and resp.status_code in (200, 201):
        try:
            data = resp.json()
            items = data.get("data", {}).get("delivery", {}).get("items", [])
            if items:
                ad_cfg = crd.AD_CFG
                if lot_name in ad_cfg:
                    stock_file = ad_cfg[lot_name].get("productsFileName", "")
                    if stock_file:
                        file_path = f"storage/products/{stock_file}"
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(items))
                        logger.info(f"[API Delivery] HStore: wrote {len(items)} items to {stock_file}")
                        return True
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"[API Delivery] HStore response parse error: {e}")
    else:
        status = resp.status_code if resp else "No response"
        logger.error(f"[API Delivery] HStore delivery failed for order {order.id}. Status: {status}")
    return False


# ---------------------------------------------------------------------------
# SMMCost Provider
# ---------------------------------------------------------------------------

def smmcost_request(params: Dict) -> Optional[requests.Response]:
    keys = get_api_keys()
    if "smmcost" not in keys or not keys["smmcost"].get("api_key"):
        logger.error("SMMCost API key not configured.")
        return None
    params["key"] = keys["smmcost"]["api_key"]
    try:
        return requests.post("https://smmcost.com/api/v2", data=params, timeout=30)
    except requests.RequestException as e:
        logger.error(f"SMMCost request failed: {e}")
        return None


def _on_confirmed(buyer: str, session: Dict):
    """Place order on SMMCost and poll until completion."""
    crd = session["cardinal"]
    order = session["order"]
    lot_cfg = get_delivery_config()
    lot_name = session["lot_name"]
    qty = session["quantity"]
    link = session["candidate"]

    crd.send_message(order.chat_id,
                     f"🚀 Boosting started!\nQuantity: {qty}\nUsername: {link}\nWe'll confirm when done.",
                     order.buyer_username)

    def worker():
        service_id = lot_cfg[lot_name].get("service_id", "")
        if not service_id:
            crd.send_message(order.chat_id, "⚠️ Error processing order. Please contact support.",
                             order.buyer_username)
            return

        # Place order
        params = {
            "action": "add",
            "service": service_id,
            "link": link,
            "quantity": qty,
        }
        resp = smmcost_request(params)
        if not resp or resp.status_code != 200:
            crd.send_message(order.chat_id, "⚠️ Error placing order. Please contact support.",
                             order.buyer_username)
            return

        try:
            data = resp.json()
            smm_order_id = data.get("order")
            if not smm_order_id:
                crd.send_message(order.chat_id, "⚠️ Error placing order. Please contact support.",
                                 order.buyer_username)
                return
        except (json.JSONDecodeError, KeyError):
            crd.send_message(order.chat_id, "⚠️ Error processing order. Please contact support.",
                             order.buyer_username)
            return

        # Poll status
        start_time = time.time()
        while time.time() - start_time < 600:
            time.sleep(15)
            status_resp = smmcost_request({"action": "status", "order": smm_order_id})
            if not status_resp or status_resp.status_code != 200:
                continue
            try:
                status_data = status_resp.json()
                status = status_data.get("status", "")
                if status == "Completed":
                    crd.send_message(order.chat_id,
                                     f"✅ Order completed!\nQuantity: {qty}\nSMM Order ID: #{smm_order_id}",
                                     order.buyer_username)
                    return
                elif status == "Partial":
                    remains = status_data.get("remains", "0")
                    delivered = qty - int(remains)
                    crd.send_message(order.chat_id,
                                     f"✅ Partial delivery\nDelivered: {delivered}/{qty}\nSMM Order ID: #{smm_order_id}",
                                     order.buyer_username)
                    return
                elif status in ("Canceled", "Error"):
                    crd.send_message(order.chat_id,
                                     "⚠️ Error processing order. Please contact support.",
                                     order.buyer_username)
                    return
            except (json.JSONDecodeError, KeyError):
                continue

        crd.send_message(order.chat_id, "⚠️ Order processing timed out. Please contact support.",
                         order.buyer_username)

    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# Event Handlers
# ---------------------------------------------------------------------------

def post_init_handler(crd: Cardinal):
    init_configs()
    logger.info(f"[API Delivery] Plugin v{VERSION} ready.")


def new_order_handler(crd: Cardinal, event: NewOrderEvent):
    """Handle new order for smmcost lots."""
    cfg = get_delivery_config()
    lot_name = event.config_section_name
    if not lot_name or lot_name not in cfg:
        return
    if cfg[lot_name].get("provider") != "smmcost":
        return
    if not cfg[lot_name].getboolean("enabled", fallback=True):
        return

    buyer = event.order.buyer_username

    def _start():
        with _lock:
            _sessions[buyer] = {
                "state": "asking",
                "order": event.order,
                "lot_name": lot_name,
                "cardinal": crd,
                "quantity": getattr(event.order, 'amount', 1),
                "candidate": None,
                "expires_at": time.time() + 600,
            }
        crd.send_message(event.order.chat_id,
                         "Please send your username or profile link",
                         event.order.buyer_username)

    threading.Thread(target=_start, daemon=True).start()


def new_message_handler(crd: Cardinal, event: NewMessageEvent):
    """Handle chat responses from buyers for smmcost."""
    msg = event.message
    if msg.author_id == crd.account.id:
        return  # ignore outgoing

    buyer = msg.author
    if not buyer:
        return

    now = time.time()

    with _lock:
        # Expire old sessions
        expired = [k for k, v in _sessions.items() if v["expires_at"] < now]
        for k in expired:
            del _sessions[k]

        if buyer not in _sessions:
            return

        session = _sessions[buyer]
        text = msg.text.strip() if msg.text else ""

        if session["state"] == "asking":
            if re.match(r"^(@\S+|https?://\S+|\S{3,50})$", text):
                session["state"] = "confirming"
                session["candidate"] = text
                crd.send_message(msg.chat_id,
                                 f'Your username is "{text}"\nSend + to confirm or - to enter again',
                                 buyer)
            else:
                crd.send_message(msg.chat_id,
                                 "Please send your username or profile link",
                                 buyer)

        elif session["state"] == "confirming":
            if text == "+":
                _sessions.pop(buyer)
                _on_confirmed(buyer, session)
            elif text == "-":
                session["state"] = "asking"
                session["candidate"] = None
                crd.send_message(msg.chat_id,
                                 "Please send your username or profile link",
                                 buyer)
            else:
                crd.send_message(msg.chat_id,
                                 f'Your username is "{session["candidate"]}"\nSend + to confirm or - to enter again',
                                 buyer)


def pre_delivery_handler(crd: Cardinal, event: NewOrderEvent):
    """Intercept delivery for smmcost lots and write placeholder."""
    cfg = get_delivery_config()
    lot_name = event.config_section_name
    if not lot_name or lot_name not in cfg:
        return

    provider = cfg[lot_name].get("provider")
    if not cfg[lot_name].getboolean("enabled", fallback=True):
        return

    # For hstore: fetch credentials and write to stock file
    if provider == "hstore":
        handle_hstore(crd, event)
        return

    # For smmcost: write placeholder so Cardinal sends interim message
    if provider == "smmcost":
        ad_cfg = crd.AD_CFG
        if lot_name in ad_cfg:
            stock_file = ad_cfg[lot_name].get("productsFileName")
            if stock_file:
                file_path = f"storage/products/{stock_file}"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write("⏳ Your order is being processed. Please follow the chat instructions.")
                logger.info(f"[API Delivery] SMMCost: wrote placeholder to {stock_file}")
        return


# ---------------------------------------------------------------------------
# Bind to Cardinal events
# ---------------------------------------------------------------------------
BIND_TO_POST_INIT = [post_init_handler]
BIND_TO_NEW_ORDER = [new_order_handler]
BIND_TO_NEW_MESSAGE = [new_message_handler]
BIND_TO_PRE_DELIVERY = [pre_delivery_handler]