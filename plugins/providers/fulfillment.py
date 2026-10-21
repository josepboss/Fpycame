"""Durable purchase identities and bounded polling for slow account suppliers."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from collections.abc import Iterator, Callable
from threading import Lock, Thread
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cardinal import Cardinal
from pathlib import Path

from .accounts import AccountSupplier, SupplierError, SupplierReview

DB_PATH = Path('storage/cache/supplier_orders.sqlite3')


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS orders (
                account INTEGER, funpay TEXT, provider TEXT, product INTEGER, quantity INTEGER,
                supplier_order TEXT, state TEXT NOT NULL, updated REAL,
                PRIMARY KEY(account,funpay))''')
            yield db
    finally:
        db.close()


def obtain(account: int, order_id: str, provider: str, product_id: int,
           quantity: int, max_wait: float = 600) -> list[str]:
    client = AccountSupplier(provider)
    # Check credentials before reserving, so missing configuration is recoverable.
    if not client.key:
        raise SupplierError('oi_provider_key')
    identity = (account, order_id)
    with database() as db:
        db.execute('INSERT OR IGNORE INTO orders VALUES (?,?,?,?,?,NULL,?,?)',
            (*identity, provider, product_id, quantity, 'creating', time.time()))
        created = db.execute('SELECT changes()').fetchone()[0] == 1
        row = db.execute('SELECT * FROM orders WHERE account=? AND funpay=?', identity).fetchone()
    if (row['provider'], row['product'], row['quantity']) != (provider, product_id, quantity):
        raise SupplierError('Order mapping changed; manual review required')
    supplier_order = row['supplier_order']
    if created:
        # Committed before POST. A crash/lost response leaves creating, which must
        # never automatically purchase again (Djekxa has no idempotency contract).
        supplier_order = client.create_order(product_id, quantity, order_id)
        with database() as db:
            db.execute("UPDATE orders SET supplier_order=?,state='pending',updated=? WHERE account=? AND funpay=?",
                       (supplier_order, time.time(), *identity))
    elif row['state'] != 'pending' or not supplier_order:
        raise SupplierError('Previous purchase or delivery needs manual review')
    start = time.monotonic()
    delay = 2.0
    while time.monotonic()-start < max_wait:
        try:
            data = client.order(supplier_order)
            status = data.get('status')
            if status == 'completed':
                try:
                    items = client.items(supplier_order, data, product_id, quantity)
                except SupplierReview:
                    with database() as db:
                        db.execute("UPDATE orders SET state='review',updated=? WHERE account=? AND funpay=?", (time.time(), *identity))
                    raise
                with database() as db:
                    updated = db.execute("UPDATE orders SET state='sending',updated=? WHERE account=? AND funpay=? AND state='pending'",
                                         (time.time(), *identity))
                    if updated.rowcount != 1:
                        raise SupplierError('Order delivery is already claimed')
                return items
            if status in ('partially-completed', 'uncompleted', 'cancelled', 'canceled', 'error', 'refund'):
                with database() as db:
                    db.execute("UPDATE orders SET state='review',updated=? WHERE account=? AND funpay=?", (time.time(), *identity))
                raise SupplierError('Supplier order incomplete; manual review required')
        except SupplierError:
            # Keep a known pending identity, but do not hide partial/final outcomes.
            with database() as db:
                state = db.execute('SELECT state FROM orders WHERE account=? AND funpay=?', identity).fetchone()[0]
            if state != 'pending':
                raise
        time.sleep(min(delay, max(0, max_wait-(time.monotonic()-start))))
        delay = min(delay*1.5, 15)
    raise SupplierError('Supplier order still pending; resume by the same FunPay order ID')


def finish(account: int, order_id: str, delivered: bool) -> None:
    with database() as db:
        db.execute("UPDATE orders SET state=?,updated=? WHERE account=? AND funpay=? AND state='sending'",
                   ('delivered' if delivered else 'delivery_review', time.time(), account, order_id))


_workers: set[tuple[int, str]] = set()
_worker_lock = Lock()


def start_worker(account: int, order_id: str, callback: Callable[[], None]) -> None:
    identity = (account, order_id)
    with _worker_lock:
        if identity in _workers:
            return
        _workers.add(identity)
    def run() -> None:
        try:
            callback()
        finally:
            with _worker_lock:
                _workers.discard(identity)
    Thread(target=run, name=f'supplier-delivery-{order_id}', daemon=True).start()


def resume_pending(cardinal: Cardinal) -> None:
    """Resume only known pending purchases; never recreate uncertain purchases."""
    from types import SimpleNamespace
    from FunPayAPI.common.enums import OrderStatuses
    from handlers import setup_event_attributes_handler, deliver_product_handler
    import logging
    logger = logging.getLogger('FPC.api_delivery')
    with database() as db:
        pending = db.execute("SELECT funpay FROM orders WHERE account=? AND state='pending'", (cardinal.account.id,)).fetchall()
    for row in pending:
        with _worker_lock:
            if (cardinal.account.id, row['funpay']) in _workers:
                continue
        try:
            order = cardinal.account.get_order(row['funpay'])
            if order.status != OrderStatuses.PAID:
                continue
            event = SimpleNamespace(order=order)
            setup_event_attributes_handler(cardinal, event)
            deliver_product_handler(cardinal, event)
        except Exception as exc:
            logger.warning('Pending supplier order %s recovery failed (%s).',row['funpay'],type(exc).__name__)
