"""Persistent supplier-to-offer links shared by the web admin and Cardinal."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager, closing
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from admin.services.offer_import import fetch_product, product_url

DB_PATH = Path("storage/cache/offer_stock.sqlite3")
PUBLICATIONS_DB = Path("storage/cache/offer_imports.sqlite3")
logger = logging.getLogger("FPC.offer_stock")


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS mappings (
                account INTEGER NOT NULL, node INTEGER NOT NULL, product INTEGER NOT NULL,
                source_url TEXT NOT NULL, title TEXT NOT NULL, offer_id INTEGER,
                last_stock INTEGER, checked REAL, state TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT NOT NULL DEFAULT '', PRIMARY KEY(account, node, product))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(mappings)")}
            if "auto_paused" not in columns:
                db.execute("ALTER TABLE mappings ADD COLUMN auto_paused INTEGER NOT NULL DEFAULT 0")
            yield db
    finally:
        db.close()


def response_offer_id(result: dict) -> int | None:
    value = result.get("offer_id")
    if isinstance(value, (int, str)) and str(value).isdigit() and int(value) > 0:
        return int(value)
    url = result.get("url")
    if isinstance(url, str):
        parts = urlsplit(url)
        if parts.netloc and parts.netloc != "funpay.com":
            return None
        if parts.path.endswith("/offerEdit") or parts.path.endswith("/offer"):
            query = parse_qs(parts.query)
            for key in ("offer", "id"):
                value = query.get(key, [""])[0]
                if value.isdigit() and int(value) > 0:
                    return int(value)
    return None


def save_mapping(account: int, node: int, product: dict, title: str,
                 offer_id: int | None = None, replace: bool = True) -> None:
    product_id, url = product_url(product["url"])
    if product_id != product["product_id"]:
        raise ValueError("Product identity mismatch")
    with database() as db:
        conflict = """DO UPDATE SET source_url=excluded.source_url, title=excluded.title,
            offer_id=excluded.offer_id, state='pending', last_error='', checked=NULL, auto_paused=0""" if replace else "DO NOTHING"
        db.execute(f"""INSERT INTO mappings(account,node,product,source_url,title,offer_id)
            VALUES (?,?,?,?,?,?) ON CONFLICT(account,node,product) {conflict}""",
            (account, node, product_id, url, title, offer_id))


def mappings(account: int | None = None) -> list[dict]:
    with database() as db:
        rows = db.execute("SELECT * FROM mappings" + (" WHERE account = ?" if account is not None else ""),
                          (account,) if account is not None else ()).fetchall()
    return [dict(row) for row in rows]


def migrate_publications(account: int) -> None:
    """Recover older successful imports using their persisted product/title snapshot."""
    if not PUBLICATIONS_DB.exists():
        return
    with closing(sqlite3.connect(PUBLICATIONS_DB)) as db:
        rows = db.execute("""SELECT p.node, d.product FROM publications p
            JOIN drafts d ON p.draft = d.id WHERE p.account = ? AND p.state = 'published'""",
            (account,)).fetchall()
    for node, raw in rows:
        product = json.loads(raw)
        save_mapping(account, node, product, product["title"], replace=False)


def check_stock(account) -> None:
    """Pause sold-out offers and resume only offers paused by this monitor."""
    cache = {}
    for mapping in mappings(account.id):
        identity = (mapping["account"], mapping["node"], mapping["product"])
        try:
            if mapping["source_url"] not in cache:
                cache[mapping["source_url"]] = fetch_product(mapping["source_url"])
            product = cache[mapping["source_url"]]
            offer_id = mapping["offer_id"]
            if not offer_id:
                # Resolve once by exact title in the specified account/category.
                matches = [lot for lot in account.get_my_subcategory_lots(mapping["node"], locale="en")
                           if lot.description == mapping["title"]]
                if len(matches) != 1:
                    raise ValueError("Offer identity requires manual mapping")
                offer_id = int(matches[0].id)
            state = "monitoring"
            auto_paused = mapping["auto_paused"]
            if product.stock == 0 or auto_paused:
                fields = account.get_lot_fields(offer_id)
                if (int(fields.lot_id) != offer_id or
                        str(fields.fields.get("node_id")) != str(mapping["node"])):
                    raise ValueError("Offer category mismatch")
                if product.stock == 0 and fields.fields.get("active") == "on":
                    # Persist ownership before saving: a lost response must not
                    # lose the information needed to resume after restocking.
                    with database() as db:
                        db.execute("UPDATE mappings SET auto_paused=1 WHERE account=? AND node=? AND product=?", identity)
                    auto_paused = 1
                    fields.active = False
                    account.save_lot(fields, locale="en")
                    # Verify the persisted checkbox state before reporting success.
                    saved = account.get_lot_fields(offer_id)
                    if saved.fields.get("active") == "on":
                        raise ValueError("Deactivation not confirmed")
                    logger.info("Deactivated FunPay offer %s: HStore product %s is out of stock.",
                                offer_id, mapping["product"])
                if product.stock == 0:
                    state = "inactive"
                elif auto_paused:
                    if fields.fields.get("active") != "on":
                        fields.active = True
                        fields.amount = max(1, min(getattr(fields, "amount", None) or product.stock, product.stock))
                        account.save_lot(fields, locale="en")
                        saved = account.get_lot_fields(offer_id)
                        if saved.fields.get("active") != "on":
                            raise ValueError("Reactivation not confirmed")
                        logger.info("Reactivated FunPay offer %s: HStore product %s is back in stock.",
                                    offer_id, mapping["product"])
                    auto_paused = 0
            with database() as db:
                db.execute("""UPDATE mappings SET offer_id=?,last_stock=?,checked=?,state=?,last_error='',auto_paused=?
                    WHERE account=? AND node=? AND product=?""",
                    (offer_id, product.stock, time.time(), state, auto_paused, *identity))
        except Exception as exc:
            # No exception payloads or credentials in the dashboard/logs.
            logger.warning("Stock check failed for product %s (%s).", mapping["product"], type(exc).__name__)
            with database() as db:
                db.execute("UPDATE mappings SET checked=?,last_error=? WHERE account=? AND node=? AND product=?",
                           (time.time(), type(exc).__name__, *identity))
