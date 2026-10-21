"""Persistent SMM service monitoring; elapsed time never means failed delivery."""
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Any

from locales.localizer import Localizer

if TYPE_CHECKING:
    from cardinal import Cardinal

DB_PATH = Path('storage/cache/smm_orders.sqlite3')
POLL_SECONDS = 120
logger = logging.getLogger('FPC.api_delivery.smm')
_worker: Thread | None = None


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS orders (
                account INTEGER NOT NULL, funpay TEXT NOT NULL, chat INTEGER NOT NULL,
                buyer TEXT NOT NULL, quantity INTEGER NOT NULL, supplier_order TEXT,
                state TEXT NOT NULL, last_status TEXT, next_check REAL NOT NULL DEFAULT 0,
                notified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(account,funpay))''')
            yield db
    finally:
        db.close()


def message(crd: Cardinal, row: dict, key: str, *args: object) -> None:
    text = Localizer().translate(key, *args, language='en')
    crd.send_message(row['chat'], text, row['buyer'], attempts=1)


def place(crd: Cardinal, order: Any, service_id: str, quantity: int, link: str,
          request: Callable) -> None:
    identity = (crd.account.id, order.id)
    row = {'chat': order.chat_id, 'buyer': order.buyer_username, 'quantity': quantity}
    with database() as db:
        inserted = db.execute('''INSERT OR IGNORE INTO orders
            (account,funpay,chat,buyer,quantity,state) VALUES (?,?,?,?,?,'creating')''',
            (*identity, row['chat'], row['buyer'], quantity))
        if inserted.rowcount != 1:
            # Duplicate confirmations, restarts and uncertain responses never buy again.
            return
    state, supplier_order, key = 'uncertain', None, 'smm_placement_verifying'
    if not service_id:
        state, key = 'failed', 'smm_start_failed'
    else:
        try:
            response = request({'action': 'add', 'service': service_id, 'link': link, 'quantity': quantity})
            if response is not None and response.status_code == 200:
                data = response.json()
                if isinstance(data, dict) and data.get('error'):
                    state, key = 'failed', 'smm_start_failed'
                elif (isinstance(data, dict) and not isinstance(data.get('order'), bool)
                      and str(data.get('order', '')).isdigit() and int(data['order']) > 0):
                    state, supplier_order, key = 'pending', str(data['order']), 'smm_started'
        except Exception as exc:
            logger.warning('SMM placement response unavailable for order %s (%s).', order.id, type(exc).__name__)
    with database() as db:
        db.execute('''UPDATE orders SET state=?,supplier_order=?,next_check=?
            WHERE account=? AND funpay=?''', (state, supplier_order, time.time()+POLL_SECONDS, *identity))
    if state == 'uncertain':
        logger.warning('SMM order %s placement needs manual verification; do not place again.', order.id)
    message(crd, row, key, quantity) if state == 'pending' else message(crd, row, key)


def check_pending(crd: Cardinal, request: Callable) -> None:
    with database() as db:
        rows = db.execute("SELECT * FROM orders WHERE account=? AND state='pending' AND next_check<=?",
                          (crd.account.id, time.time())).fetchall()
    for raw in rows:
        row = dict(raw)
        identity = (row['account'], row['funpay'])
        # Claim this polling interval across concurrent checks/processes.
        with database() as db:
            claimed = db.execute("""UPDATE orders SET next_check=? WHERE account=? AND funpay=?
                AND state='pending' AND next_check<=?""", (time.time()+POLL_SECONDS, *identity, time.time()))
            if claimed.rowcount != 1:
                continue
        try:
            response = request({'action': 'status', 'order': row['supplier_order']})
            if response is None or response.status_code != 200:
                continue
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('status'), str):
                continue
            status = data['status'].strip().casefold()
            state, key, args = 'pending', None, ()
            if status == 'completed':
                state, key, args = 'completed', 'smm_completed', (row['quantity'], row['supplier_order'])
            elif status == 'partial':
                remains = data.get('remains')
                if isinstance(remains, bool) or not str(remains).isdigit() or not 0 <= int(remains) <= row['quantity']:
                    continue
                state, key, args = 'partial', 'smm_partial', (row['quantity']-int(remains), row['quantity'], row['supplier_order'])
            elif status in ('canceled', 'cancelled', 'error', 'failed'):
                state, key = 'failed', 'smm_service_failed'
            with database() as db:
                updated = db.execute('''UPDATE orders SET state=?,last_status=?,notified=?
                    WHERE account=? AND funpay=? AND state='pending' ''',
                    (state, status, int(key is not None), *identity))
            if key and updated.rowcount == 1:
                # Persist the terminal status before notifying to prevent repeated
                # completion/failure messages after a restart or lost chat response.
                message(crd, row, key, *args)
        except Exception as exc:
            # Provider outages, malformed replies and timeouts leave services pending.
            logger.warning('SMM status check deferred for order %s (%s).', row['funpay'], type(exc).__name__)


def start_monitor(crd: Cardinal, request: Callable, enabled: Callable[[], bool]) -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    def loop() -> None:
        while True:
            try:
                if enabled():
                    check_pending(crd, request)
            except Exception as exc:
                logger.warning('SMM monitor check deferred (%s).', type(exc).__name__)
            time.sleep(60)
    _worker = Thread(target=loop, name='smm-service-monitor', daemon=True)
    _worker.start()
