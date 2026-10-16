"""Monitor imported HStore products in Cardinal's existing background process."""
from __future__ import annotations

import logging
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING

from admin.services.offer_stock import check_stock, migrate_publications

if TYPE_CHECKING:
    from cardinal import Cardinal

NAME = "Imported Offer Stock Monitor"
VERSION = "1.0.0"
DESCRIPTION = "Deactivate imported FunPay offers when HStore stock reaches zero."
CREDITS = "Cardinal"
UUID = "80921053-5ce8-4c4f-b782-80dff7af45c4"
SETTINGS_PAGE = False
BIND_TO_DELETE = None
logger = logging.getLogger("FPC.offer_stock")
_worker: Thread | None = None


def start_monitor(cardinal: Cardinal) -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return

    def loop() -> None:
        while True:
            plugin = cardinal.plugins.get(UUID)
            if plugin is not None and plugin.enabled:
                try:
                    migrate_publications(cardinal.account.id)
                    check_stock(cardinal.account)
                except Exception as exc:
                    logger.warning("Stock monitor cycle failed (%s).", type(exc).__name__)
            sleep(60)

    _worker = Thread(target=loop, name="offer-stock-monitor", daemon=True)
    _worker.start()


BIND_TO_POST_INIT = [start_monitor]
