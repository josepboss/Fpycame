"""Private sales snapshots; never fetch or retain purchased credentials."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from collections.abc import Iterator

DB_PATH = Path('storage/cache/sales_analytics.sqlite3')
_LOCK = threading.Lock()
logger = logging.getLogger('FPC.admin.analytics')


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS sales (account INTEGER, id TEXT, title TEXT, category TEXT, currency TEXT, price TEXT, units INTEGER, status TEXT, date TEXT, PRIMARY KEY(account,id))')
    db.execute('CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY, account INTEGER, updated REAL, state TEXT)')
    try:
        with db:
            yield db
    finally:
        db.close()


def sync_sales() -> None:
    """Replace only after a complete fetch; a failure preserves the last snapshot."""
    from admin.services.offer_import import make_account
    try:
        with database() as db:
            db.execute("INSERT INTO snapshot(id,state) VALUES(1,'running') ON CONFLICT(id) DO UPDATE SET state='running'")
        account = make_account()
        cursor = None
        seen = set()
        rows = {}
        categories = None
        for _ in range(1000):
            next_cursor, orders, locale, categories = account.get_sales(start_from=cursor, locale='en', subcategories=categories)
            for order in orders:
                # Quantity is part of the historical order description, not the offer title.
                title = re.sub(r',?\s+\d[\d ]*\s+(?:pcs?\.|шт\.)\s*$', '', order.description).strip()
                rows[order.id] = (account.id, order.id, title, order.subcategory_name,
                    order.currency.name, str(order.price), order.amount or 1,
                    order.status.name, order.date.isoformat())
            if not next_cursor:
                break
            if next_cursor in seen:
                raise ValueError('Repeated sales cursor')
            seen.add(next_cursor)
            cursor = next_cursor
            time.sleep(.3)
        else:
            raise ValueError('Incomplete sales history')
        with database() as db:
            db.execute('DELETE FROM sales WHERE account=?', (account.id,))
            db.executemany('INSERT INTO sales VALUES(?,?,?,?,?,?,?,?,?)', rows.values())
            db.execute("UPDATE snapshot SET account=?,updated=?,state='ready' WHERE id=1", (account.id,time.time()))
    except Exception as exc:
        logger.error('Sales analytics refresh failed (%s).',type(exc).__name__)
        with database() as db:
            db.execute("UPDATE snapshot SET state='failed' WHERE id=1")
    finally:
        _LOCK.release()


def refresh() -> bool:
    if not _LOCK.acquire(blocking=False):
        return False
    threading.Thread(target=sync_sales, daemon=True, name='sales-analytics').start()
    return True


def report(days: int = 30, ranking: str = 'sales') -> dict:
    from datetime import datetime, timedelta
    with database() as db:
        meta = db.execute('SELECT * FROM snapshot WHERE id=1').fetchone()
        rows = db.execute('SELECT * FROM sales WHERE account=?', (meta['account'],)).fetchall() if meta else []
    cutoff = (datetime.now()-timedelta(days=days)).isoformat() if days else ''
    totals, offers = {}, {}
    for row in rows:
        if row['date'] < cutoff:
            continue
        currency = row['currency']
        total = totals.setdefault(currency,dict(currency=currency,sales=0,units=0,revenue=Decimal(0),pending=0,refunds=0))
        if row['status'] == 'REFUNDED':
            total['refunds'] += 1
        elif row['status'] == 'PAID':
            total['pending'] += 1
        elif row['status'] == 'CLOSED':
            total['sales'] += 1
            total['units'] += row['units']
            total['revenue'] += Decimal(row['price'])
            item = offers.setdefault((currency,row['category'],row['title']),dict(currency=currency,title=row['title'],category=row['category'],sales=0,units=0,revenue=Decimal(0)))
            item['sales'] += 1
            item['units'] += row['units']
            item['revenue'] += Decimal(row['price'])
    # Never rank monetary amounts against a different currency.
    groups = {currency:sorted((v for v in offers.values() if v['currency']==currency),key=lambda v:(v[ranking],v['sales']),reverse=True) for currency in totals}
    return dict(meta=dict(meta) if meta else None,totals=list(totals.values()),groups=groups)
