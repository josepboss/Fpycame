"""Authenticated preview/publish workflow with durable duplicate protection."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator

import configparser
import json
import logging
import secrets
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from admin.templating import templates
from fastapi.responses import Response

from admin.auth import get_csrf_token, validate_csrf_token
from admin.services.offer_import import (ImportFailure, fetch_product, make_account,
                                         prepare_offer, selling_price, submit_offer)
from locales.localizer import Localizer
from admin.services import offer_stock
from admin.services.delivery_mapping import check_import_mapping, save_delivery_mapping, MappingConflict
from admin.routes.lots import restart_cardinal
from urllib.parse import parse_qs, urlsplit

router = APIRouter()
logger = logging.getLogger("FPC.admin.import")
DB_PATH = Path("storage/cache/offer_imports.sqlite3")


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, product TEXT NOT NULL, created REAL NOT NULL)")
    db.execute("""CREATE TABLE IF NOT EXISTS publications (
        account INTEGER, node INTEGER, product INTEGER, draft TEXT, state TEXT,
        PRIMARY KEY(account, node, product))""")
    try:
        with db:
            yield db
    finally:
        db.close()


def render(request: Request, **context) -> Response:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read("configs/_main.cfg", encoding="utf-8")
    lang = cfg.get("Other", "language", fallback="en")
    localizer = Localizer()
    stock_mappings = offer_stock.mappings()
    for mapping in stock_mappings:
        mapping['stale'] = mapping['checked'] is not None and time.time() - mapping['checked'] > 300
    context.update(request=request, csrf_token=get_csrf_token(), stock_mappings=stock_mappings,
                   _=lambda key: localizer.translate(key, language=lang))
    return templates.TemplateResponse(request=request, name="import_offers.html", context=context)


def csrf(token: str) -> None:
    if not token or not validate_csrf_token(token):
        raise HTTPException(status_code=403, detail=Localizer().translate("oi_csrf", language="en"))


@router.get("/")
def import_page(request: Request) -> Response:
    return render(request)


@router.post("/preview")
def preview(request: Request, product_url: str = Form(...), csrf_token: str = Form("")) -> Response:
    csrf(csrf_token)
    try:
        product = fetch_product(product_url)
        draft = secrets.token_urlsafe(24)
        with database() as db:
            db.execute("DELETE FROM drafts WHERE created < ?", (time.time() - 86400,))
            db.execute("INSERT INTO drafts VALUES (?, ?, ?)",
                       (draft, json.dumps(product.to_dict()), time.time()))
        return render(request, product=product.to_dict(), draft=draft)
    except ImportFailure as exc:
        return render(request, error=str(exc), source_url=product_url)


@router.post("/publish")
def publish(request: Request, draft: str = Form(...), title: str = Form(...),
            description: str = Form(...), node: int = Form(...), quantity: int = Form(...),
            markup: str = Form(...), csrf_token: str = Form(""),
            republish_deleted: bool = Form(False)) -> Response:
    csrf(csrf_token)
    with database() as db:
        row = db.execute("SELECT * FROM drafts WHERE id = ?", (draft,)).fetchone()
    if row is None or row["created"] < time.time() - 86400:
        return render(request, error="oi_expired")
    product = json.loads(row["product"])
    # HTML form submission serializes textarea newlines as CRLF, while the
    # browser's value/counter uses LF. Normalize before counting or sending.
    description = description.replace("\r\n", "\n").replace("\r", "\n").strip()
    values = dict(title=title, description=description, node=node, quantity=quantity, markup=markup)
    context = dict(product=product, draft=draft, values=values)
    claimed = False
    account = None
    try:
        title, description = title.strip(), description.strip()
        if not title or len(title) > 100 or not 1 <= node <= 1000000:
            raise ImportFailure("oi_invalid")
        if not description or len(description) > 817:
            raise ImportFailure("oi_description_length")
        if not 1 <= quantity <= product["stock"]:
            raise ImportFailure("oi_stock")
        price = selling_price(product["price"], markup)
        current = fetch_product(product["url"])
        if current.price != product["price"] or current.stock < quantity:
            raise ImportFailure("oi_changed")
        account = make_account()
        # Fetch the live editor to retain fresh CSRF and anti-stale form fields.
        fields = prepare_offer(account, node, title, description, price, quantity)
        # Detect offers made outside this importer as well.
        existing = account.get_my_subcategory_lots(node, locale="en")
        if any(lot.description == title for lot in existing):
            raise ImportFailure("oi_duplicate")
        try:
            check_import_mapping(title, product["product_id"])
        except MappingConflict:
            raise ImportFailure("oi_delivery_conflict")
        with database() as db:
            try:
                db.execute("INSERT INTO publications VALUES (?, ?, ?, ?, ?)",
                           (account.id, node, product["product_id"], draft, "publishing"))
            except sqlite3.IntegrityError:
                # Only an explicitly confirmed deletion of a completed publish
                # permits reuse. Never retry pending/uncertain submissions.
                if not republish_deleted:
                    raise ImportFailure("oi_duplicate")
                updated = db.execute(
                    """UPDATE publications SET draft = ?, state = 'publishing'
                       WHERE account = ? AND node = ? AND product = ?
                       AND state = 'published' AND draft != ?""",
                    (draft, account.id, node, product["product_id"], draft))
                if updated.rowcount != 1:
                    raise ImportFailure("oi_duplicate")
        claimed = True
        result = submit_offer(account, fields)
        offer_stock.save_mapping(account.id, node, product, title, offer_stock.response_offer_id(result), markup=markup)
        with database() as db:
            db.execute("UPDATE publications SET state = 'published' WHERE draft = ?", (draft,))
        # Publishing already succeeded. Mapping/restart errors must never invite
        # another publish and create a duplicate public offer.
        delivery_status = "oi_delivery_ready"
        try:
            save_delivery_mapping(title, 'hstore', str(product["product_id"]), True, imported=True)
            if not restart_cardinal():
                delivery_status = "oi_delivery_restart"
        except Exception as exc:
            logger.error("Published offer delivery setup failed (%s).", type(exc).__name__)
            delivery_status = "oi_delivery_failed"
        return render(request, success=True, trade_url=f"https://funpay.com/en/lots/{node}/trade",
                      published_title=title, price=price, delivery_status=delivery_status)
    except ImportFailure as exc:
        code = str(exc)
        if claimed and code == "oi_rejected":
            with database() as db:
                db.execute("DELETE FROM publications WHERE draft = ?", (draft,))
        elif claimed:
            code = "oi_uncertain"
        return render(request, error=code, error_detail=exc.details if code == "oi_rejected" else "", **context)
    except Exception as exc:
        # Do not expose credentials or remote HTML in an HTTP error response.
        logger.error("Offer import failed (%s; exception=%s).",
                     "publish outcome uncertain" if claimed else "preflight", type(exc).__name__)
        return render(request, error="oi_uncertain" if claimed else "oi_connection", **context)


@router.post("/track")
def track_offer(request: Request, product_url: str = Form(...), offer_url: str = Form(...),
                csrf_token: str = Form("")) -> Response:
    csrf(csrf_token)
    try:
        parts = urlsplit(offer_url)
        query = parse_qs(parts.query)
        if (parts.scheme != "https" or parts.netloc != "funpay.com" or
                parts.path not in ("/lots/offerEdit", "/en/lots/offerEdit")):
            raise ImportFailure("oi_bad_mapping")
        node, offer_id = int(query["node"][0]), int(query["offer"][0])
        if node <= 0 or offer_id <= 0:
            raise ImportFailure("oi_bad_mapping")
        product = fetch_product(product_url)
        account = make_account()
        fields = account.get_lot_fields(offer_id)
        if int(fields.lot_id) != offer_id or str(fields.fields.get("node_id")) != str(node):
            raise ImportFailure("oi_bad_mapping")
        title = fields.title_en or fields.title_ru
        offer_stock.save_mapping(account.id, node, product.to_dict(), title, offer_id)
        return render(request, mapping_saved=True)
    except (ValueError, KeyError, IndexError):
        return render(request, error="oi_bad_mapping")
    except Exception:
        return render(request, error="oi_connection")
