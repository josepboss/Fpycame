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
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from admin.templating import templates
from fastapi.responses import Response, RedirectResponse

from admin.auth import get_csrf_token, validate_csrf_token
from admin.services.offer_import import (ImportFailure, fetch_product, make_account,
                                         prepare_offer, selling_price, submit_offer, usd_cost)
from locales.localizer import Localizer
from admin.services import offer_stock
from admin.services.delivery_mapping import check_import_mapping, save_delivery_mapping, MappingConflict, remove_delivery_mappings
from admin.routes.lots import restart_cardinal
from urllib.parse import parse_qs, urlsplit

from admin.services.offer_translation import bilingual_listing, bilingual_text, CYRILLIC

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
            from admin.services.provider_schema import scope_provider
            scope_provider(db, 'publications')
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
def import_page(request: Request, markup_saved: bool = False, removed: bool = False, restart_failed: bool = False) -> Response:
    return render(request, markup_saved=markup_saved, removed=removed, restart_failed=restart_failed)


@router.post("/preview")
def preview(request: Request, product_url: str = Form(...), csrf_token: str = Form("")) -> Response:
    csrf(csrf_token)
    try:
        product = fetch_product(product_url)
        listing = None
        translation_error = None
        try:
            listing = bilingual_listing(product.title, product.description)
        except ImportFailure:
            translation_error = 'oi_translation_failed'
            listing = {f'title_{"ru" if CYRILLIC.search(product.title) else "en"}': product.title[:100],
                       f'description_{"ru" if CYRILLIC.search(product.description) else "en"}': product.description[:817]}
        snapshot = product.to_dict()
        snapshot['listing'] = listing
        draft = secrets.token_urlsafe(24)
        with database() as db:
            db.execute("DELETE FROM drafts WHERE created < ?", (time.time() - 86400,))
            db.execute("INSERT INTO drafts VALUES (?, ?, ?)",
                       (draft, json.dumps(snapshot), time.time()))
        return render(request, product=snapshot, draft=draft, error=translation_error)
    except ImportFailure as exc:
        return render(request, error=str(exc), source_url=product_url)


@router.post("/publish")
def publish(request: Request, draft: str = Form(...), title: str = Form(...),
            description: str = Form(...), node: int = Form(...), quantity: int = Form(...),
            markup: str = Form(...), csrf_token: str = Form(""),
            republish_deleted: bool = Form(False), rub_per_usd: Annotated[str, Form()] = '',
            title_ru: Annotated[str, Form()] = '', description_ru: Annotated[str, Form()] = '') -> Response:
    csrf(csrf_token)
    with database() as db:
        row = db.execute("SELECT * FROM drafts WHERE id = ?", (draft,)).fetchone()
    if row is None or row["created"] < time.time() - 86400:
        return render(request, error="oi_expired")
    product = json.loads(row["product"])
    provider = product.get("provider", "hstore")
    # HTML form submission serializes textarea newlines as CRLF, while the
    # browser's value/counter uses LF. Normalize before counting or sending.
    description = description.replace("\r\n", "\n").replace("\r", "\n").strip()
    values = dict(title=title, description=description, node=node, quantity=quantity, markup=markup, rub_per_usd=rub_per_usd, title_ru=title_ru, description_ru=description_ru)
    context = dict(product=product, draft=draft, values=values)
    claimed = False
    account = None
    try:
        title, description = title.strip(), description.strip()
        if not title or len(title) > 100 or not 1 <= node <= 1000000:
            raise ImportFailure("oi_invalid")
        if not description or len(description) > 817:
            raise ImportFailure("oi_description_length")
        if product.get('minimum_order', 1) > 1:
            raise ImportFailure('oi_minimum_product')
        if not product.get("minimum_order", 1) <= quantity <= product["stock"]:
            raise ImportFailure("oi_stock")
        if CYRILLIC.search(title) or CYRILLIC.search(description):
            # Support an older, already-open draft that still submits Russian
            # source text through the formerly shared fields.
            listing = bilingual_listing(title, description)
            title, description = listing['title_en'], listing['description_en']
            title_ru = title_ru or listing['title_ru']
            description_ru = description_ru or listing['description_ru']
        title_ru = title_ru.strip() or bilingual_text(title,100)['ru']
        description_ru = description_ru.replace('\r\n','\n').replace('\r','\n').strip() or bilingual_text(description,817)['ru']
        if not 1 <= len(title_ru) <= 100 or not 1 <= len(description_ru) <= 817:
            raise ImportFailure('oi_translation_limits')
        values.update(title=title,description=description,title_ru=title_ru,description_ru=description_ru)
        price = selling_price(usd_cost(product["price"], product.get("currency", "USD"), rub_per_usd), markup)
        current = (fetch_product(product["url"]) if provider == "hstore" else
                   fetch_product(product["url"], product_id=product["product_id"]))
        if current.price != product["price"] or current.stock < quantity:
            raise ImportFailure("oi_changed")
        account = make_account()
        # Fetch the live editor to retain fresh CSRF and anti-stale form fields.
        fields = prepare_offer(account, node, title, description, price, quantity, title_ru=title_ru, description_ru=description_ru)
        # Detect offers made outside this importer as well.
        existing = account.get_my_subcategory_lots(node, locale="en")
        if any(lot.description in (title,title_ru) for lot in existing):
            raise ImportFailure("oi_duplicate")
        try:
            for mapped_title in {title,title_ru}:
                check_import_mapping(mapped_title, product["product_id"], provider)
        except MappingConflict:
            raise ImportFailure("oi_delivery_conflict")
        with database() as db:
            try:
                db.execute("INSERT INTO publications(account,node,product,draft,state,provider) VALUES (?, ?, ?, ?, ?, ?)",
                           (account.id, node, product["product_id"], draft, "publishing", provider))
            except sqlite3.IntegrityError:
                # Only an explicitly confirmed deletion of a completed publish
                # permits reuse. Never retry pending/uncertain submissions.
                if not republish_deleted:
                    raise ImportFailure("oi_duplicate")
                updated = db.execute(
                    """UPDATE publications SET draft = ?, state = 'publishing'
                       WHERE account = ? AND node = ? AND product = ? AND provider = ?
                       AND state = 'published' AND draft != ?""",
                    (draft, account.id, node, product["product_id"], provider, draft))
                if updated.rowcount != 1:
                    raise ImportFailure("oi_duplicate")
        claimed = True
        result = submit_offer(account, fields)
        offer_stock.save_mapping(account.id, node, product, title, offer_stock.response_offer_id(result), markup=markup, rub_per_usd=rub_per_usd or None)
        with database() as db:
            db.execute("UPDATE publications SET state = 'published' WHERE draft = ?", (draft,))
        # Publishing already succeeded. Mapping/restart errors must never invite
        # another publish and create a duplicate public offer.
        delivery_status = "oi_delivery_ready"
        try:
            save_delivery_mapping(title, provider, str(product["product_id"]), True, imported=True)
            from admin.services.delivery_mapping import alias_delivery_mapping
            alias_delivery_mapping(title,title_ru,product['product_id'],provider)
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


@router.post('/markup')
def update_markup(request: Request, account_id: int = Form(...), node: int = Form(...),
                  product_id: int = Form(...), markup: str = Form(...), csrf_token: str = Form(''),
                  provider: Annotated[str, Form()] = 'hstore', rub_per_usd: Annotated[str, Form()] = '') -> Response:
    csrf(csrf_token)
    try:
        if rub_per_usd:
            usd_cost('1','RUB',rub_per_usd)
        selling_price('1',markup)
    except ImportFailure as exc:
        return render(request,error=str(exc))
    with offer_stock.database() as db:
        result=db.execute("""UPDATE mappings SET markup=?,markup_confirmed=1,price_sync_pending=1,rub_per_usd=COALESCE(?,rub_per_usd)
            WHERE account=? AND node=? AND product=? AND provider=?""",(markup,rub_per_usd or None,account_id,node,product_id,provider))
        if result.rowcount != 1:
            raise HTTPException(status_code=404,detail='Mapping not found')
    return RedirectResponse('/imports/?markup_saved=true',status_code=303)


@router.get('/remove')
def remove_page(request: Request, account_id: int, node: int, product_id: int, provider: str = 'hstore') -> Response:
    with offer_stock.database() as db:
        row=db.execute('SELECT * FROM mappings WHERE account=? AND node=? AND product=? AND provider=? AND state!=?',
                       (account_id,node,product_id,provider,'removed')).fetchone()
    if row is None:
        raise HTTPException(status_code=404,detail='Mapping not found')
    return render(request,remove_mapping=dict(row))


@router.post('/remove')
def remove_import(request: Request, account_id: int = Form(...), node: int = Form(...),
                  product_id: int = Form(...), csrf_token: str = Form(''), confirm: bool = Form(False),
                  provider: Annotated[str, Form()] = 'hstore') -> Response:
    csrf(csrf_token)
    if not confirm:
        raise HTTPException(status_code=400,detail=Localizer().translate('oi_remove_confirm',language='en'))
    identity=(account_id,node,product_id,provider)
    with offer_stock.database() as db:
        row=db.execute('SELECT * FROM mappings WHERE account=? AND node=? AND product=? AND provider=?',identity).fetchone()
        if row is None or row['state']=='removed':
            return RedirectResponse('/imports/?removed=true',status_code=303)
        mapping=dict(row)
        db.execute("UPDATE mappings SET state='removing' WHERE account=? AND node=? AND product=? AND provider=?",identity)
    try:
        with offer_stock.action_lock(*identity):
            with offer_stock.database() as db:
                current=db.execute('SELECT state FROM mappings WHERE account=? AND node=? AND product=? AND provider=?',identity).fetchone()
            if current is None or current['state']=='removed':
                return RedirectResponse('/imports/?removed=true',status_code=303)
            account=make_account()
            if account.id!=account_id:
                raise ValueError('Configured account differs from mapping')
            offer_id=mapping['offer_id']
            if not offer_id:
                candidates=[lot for lot in account.get_my_subcategory_lots(node,locale='en') if lot.description==mapping['title']]
                if len(candidates)!=1:
                    raise ValueError('Offer identity is unresolved')
                offer_id=int(candidates[0].id)
            # Confirm this ID belongs to the configured account and category before deletion.
            existing=account.get_my_subcategory_lots(node,locale='en')
            if any(int(lot.id)==offer_id for lot in existing):
                fields=account.get_lot_fields(offer_id)
                if str(fields.fields.get('node_id'))!=str(node) or int(fields.lot_id)!=offer_id:
                    raise ValueError('Offer identity mismatch')
                account.delete_lot(offer_id)
                if any(int(lot.id)==offer_id for lot in account.get_my_subcategory_lots(node,locale='en')):
                    raise ValueError('Offer deletion not confirmed')
            other_titles={m['title'] for m in offer_stock.mappings(account_id) if (m['account'],m['node'],m['product'],m['provider']) != identity}
            remove_delivery_mappings(mapping['title'],product_id,other_titles,provider)
            with database() as db:
                db.execute('DELETE FROM publications WHERE account=? AND node=? AND product=? AND provider=?',identity)
            with offer_stock.database() as db:
                db.execute("UPDATE mappings SET state='removed',last_error='' WHERE account=? AND node=? AND product=? AND provider=?",identity)
            restarted=restart_cardinal()
            return RedirectResponse('/imports/?removed=true'+('' if restarted else '&restart_failed=true'),status_code=303)
    except Exception as exc:
        logger.error('Imported offer removal failed (%s).',type(exc).__name__)
        with offer_stock.database() as db:
            db.execute("UPDATE mappings SET state='removal_error',last_error=? WHERE account=? AND node=? AND product=? AND provider=?",
                       (type(exc).__name__,*identity))
        return render(request,remove_mapping=mapping,error='oi_remove_failed')
