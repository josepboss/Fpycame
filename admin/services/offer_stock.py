"""Persistent supplier-to-offer links shared by the web admin and Cardinal."""
from __future__ import annotations

from decimal import Decimal
import json
import os
import logging
import sqlite3
import time
from contextlib import contextmanager, closing
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from admin.services.offer_import import fetch_live_stock, product_url, fetch_product, selling_price
from admin.services.delivery_mapping import alias_delivery_mapping

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
            for name in ('supplier_title', 'supplier_price', 'markup'):
                if name not in columns:
                    db.execute(f'ALTER TABLE mappings ADD COLUMN {name} TEXT')
            for name in ('markup_confirmed', 'price_sync_pending'):
                if name not in columns:
                    db.execute(f'ALTER TABLE mappings ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
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
                 offer_id: int | None = None, replace: bool = True, markup: str | None = None) -> None:
    product_id, url = product_url(product["url"])
    if product_id != product["product_id"]:
        raise ValueError("Product identity mismatch")
    with database() as db:
        conflict = """DO UPDATE SET source_url=excluded.source_url, title=excluded.title,
            offer_id=excluded.offer_id, state='pending', last_error='', checked=NULL, auto_paused=0, supplier_title=excluded.supplier_title, supplier_price=excluded.supplier_price, markup=excluded.markup, markup_confirmed=excluded.markup_confirmed, price_sync_pending=0""" if replace else "DO NOTHING"
        db.execute(f"""INSERT INTO mappings(account,node,product,source_url,title,offer_id,supplier_title,supplier_price,markup,markup_confirmed)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(account,node,product) {conflict}""",
            (account, node, product_id, url, title, offer_id, product.get("title",title),product.get("price"),markup,int(markup is not None)))


def mappings(account: int | None = None) -> list[dict]:
    with database() as db:
        rows = db.execute("SELECT * FROM mappings WHERE state != 'removed'" + (" AND account = ?" if account is not None else ""),
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
        with database() as db:
            db.execute("""UPDATE mappings SET supplier_title=COALESCE(supplier_title,?),
                supplier_price=COALESCE(supplier_price,?) WHERE account=? AND node=? AND product=?""",
                (product['title'],product['price'],account,node,product['product_id']))


def check_stock(account) -> None:
    """Pause sold-out offers and resume only offers paused by this monitor."""
    cache = {}
    for mapping in mappings(account.id):
        identity = (mapping["account"], mapping["node"], mapping["product"])
        with action_lock(*identity):
            with database() as db:
                status=db.execute('SELECT state FROM mappings WHERE account=? AND node=? AND product=?',identity).fetchone()
            if status is None or status['state'] in ('removed','removing','removal_error'):
                continue
            try:
                if mapping["source_url"] not in cache:
                    cache[mapping["source_url"]] = fetch_live_stock(mapping["product"])
                stock = cache[mapping["source_url"]]
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
                if stock == 0 or auto_paused:
                    fields = account.get_lot_fields(offer_id)
                    if (int(fields.lot_id) != offer_id or
                            str(fields.fields.get("node_id")) != str(mapping["node"])):
                        raise ValueError("Offer category mismatch")
                    if stock == 0 and fields.fields.get("active") == "on":
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
                    if stock == 0:
                        state = "inactive"
                    elif auto_paused:
                        if fields.fields.get("active") != "on":
                            fields.active = True
                            fields.amount = max(1, min(getattr(fields, "amount", None) or stock, stock))
                            account.save_lot(fields, locale="en")
                            saved = account.get_lot_fields(offer_id)
                            if saved.fields.get("active") != "on":
                                raise ValueError("Reactivation not confirmed")
                            logger.info("Reactivated FunPay offer %s: HStore product %s is back in stock.",
                                        offer_id, mapping["product"])
                        auto_paused = 0
                with database() as db:
                    db.execute("""UPDATE mappings SET offer_id=?,last_stock=?,checked=?,state=?,last_error=CASE WHEN last_error LIKE 'Metadata:%' THEN last_error ELSE '' END,auto_paused=?
                        WHERE account=? AND node=? AND product=?""",
                        (offer_id, stock, time.time(), state, auto_paused, *identity))
            except Exception as exc:
                # No exception payloads or credentials in the dashboard/logs.
                logger.warning("Stock check failed for product %s (%s).", mapping["product"], type(exc).__name__)
                with database() as db:
                    db.execute("UPDATE mappings SET checked=?,last_error=? WHERE account=? AND node=? AND product=?",
                               (time.time(), type(exc).__name__, *identity))


def sync_metadata(account, cardinal=None) -> None:
    """Follow supplier title/price changes without changing stable offer/product IDs."""
    for mapping in mappings(account.id):
        if not mapping['offer_id']:
            continue
        identity=(mapping['account'],mapping['node'],mapping['product'])
        with action_lock(*identity):
            with database() as db:
                status=db.execute('SELECT state FROM mappings WHERE account=? AND node=? AND product=?',identity).fetchone()
            if status is None or status['state'] in ('removed','removing','removal_error'):
                continue
            try:
                product=fetch_product(mapping['source_url'])
                if product.product_id != mapping['product']:
                    raise ValueError('Supplier product mismatch')
                target_title=product.title[:100].rstrip()
                title_changed=product.title != (mapping['supplier_title'] or mapping['title'])
                price_changed=bool(mapping['price_sync_pending']) or (mapping['supplier_price'] is not None and Decimal(product.price) != Decimal(mapping['supplier_price']))
                if title_changed and (not target_title or '\n' in product.title or '\r' in product.title):
                    raise ValueError('Supplier title is not a valid FunPay title')
                markup=mapping['markup'] if mapping['markup_confirmed'] else None
                fields=None
                if title_changed or (price_changed and markup is not None):
                    fields=account.get_lot_fields(mapping['offer_id'])
                    if int(fields.lot_id)!=mapping['offer_id'] or str(fields.fields.get('node_id'))!=str(mapping['node']):
                        raise ValueError('Offer identity mismatch')
                changed=False
                if title_changed:
                    existing=account.get_my_subcategory_lots(mapping['node'],locale='en')
                    if any(lot.description==target_title and int(lot.id)!=mapping['offer_id'] for lot in existing):
                        raise ValueError('New title conflicts with another FunPay offer')
                    alias_delivery_mapping(mapping['title'],target_title,mapping['product'])
                    if cardinal is not None:
                        from Utils.config_loader import load_auto_delivery_config
                        from admin.services.delivery_mapping import AUTO_CFG
                        cardinal.AD_CFG=load_auto_delivery_config(str(AUTO_CFG))
                    fields.title_en=target_title
                    fields.title_ru=target_title
                    changed=True
                target_price=None
                if price_changed and markup is not None:
                    target_price=selling_price(product.price,markup)
                    fields.price=float(target_price)
                    changed=True
                if changed:
                    account.save_lot(fields,locale='en')
                    saved=account.get_lot_fields(mapping['offer_id'])
                    if title_changed and (saved.title_en!=target_title or saved.title_ru!=target_title):
                        raise ValueError('Title update not confirmed')
                    if target_price and Decimal(str(saved.price))!=Decimal(target_price):
                        raise ValueError('Price update not confirmed')
                with database() as db:
                    db.execute("""UPDATE mappings SET title=?,supplier_title=?,supplier_price=?,source_url=?,
                        price_sync_pending=CASE WHEN markup IS ? AND markup_confirmed=? THEN 0 ELSE price_sync_pending END,
                        last_error=CASE WHEN last_error LIKE 'Metadata:%' THEN '' ELSE last_error END
                        WHERE account=? AND node=? AND product=?""",
                        (target_title if title_changed else mapping['title'],product.title,product.price,product.url,mapping['markup'],mapping['markup_confirmed'],*identity))
            except Exception as exc:
                logger.warning('Offer metadata sync failed for product %s (%s).',mapping['product'],type(exc).__name__)
                with database() as db:
                    db.execute('UPDATE mappings SET last_error=? WHERE account=? AND node=? AND product=?',
                               ('Metadata:'+type(exc).__name__,*identity))


@contextmanager
def action_lock(account: int, node: int, product: int) -> Iterator[None]:
    """Serialize remote mutations with removal across admin/Cardinal processes."""
    folder=DB_PATH.parent/'offer_locks'
    folder.mkdir(parents=True,exist_ok=True)
    with (folder/f'{account}-{node}-{product}.lock').open('a+') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write('0');stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream,fcntl.LOCK_UN)
