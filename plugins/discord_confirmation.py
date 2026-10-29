"""Confirmation requests only after a recorded successful delivery and 24h wait."""
from __future__ import annotations

import logging
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING

from Utils.discord_confirmation import check_reminders, record_delivery

if TYPE_CHECKING:
    from cardinal import Cardinal
    from FunPayAPI.updater.events import NewOrderEvent

NAME = 'Discord Confirmation Reminders'
VERSION = '1.0.0'
DESCRIPTION = 'Request confirmation once for orders unconfirmed 24h after successful delivery.'
CREDITS = 'Cardinal'
UUID = '310d7af6-6764-4ea9-a71a-d051b83e7c0a'
SETTINGS_PAGE = False
BIND_TO_DELETE = None
logger = logging.getLogger('FPC.discord_confirmation')
_worker: Thread | None = None


def delivered(cardinal: Cardinal, event: NewOrderEvent) -> None:
    if not getattr(event,'delivered',False) or getattr(event,'error',False):
        return
    # SMM placeholders acknowledge placement, not completed service delivery.
    from plugins.api_delivery import get_delivery_config
    cfg=get_delivery_config()
    name=getattr(event,'config_section_name','')
    if name in cfg and cfg[name].get('provider')=='smmcost':
        return
    record_delivery(cardinal.account.id,event.order.id)


def start(cardinal: Cardinal) -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    def loop() -> None:
        while True:
            plugin=cardinal.plugins.get(UUID)
            if plugin is not None and plugin.enabled:
                try:
                    check_reminders(cardinal.account)
                except Exception as exc:
                    logger.warning('Discord confirmation cycle deferred (%s).',type(exc).__name__)
            sleep(60)
    _worker=Thread(target=loop,name='discord-confirmation',daemon=True)
    _worker.start()


BIND_TO_POST_INIT = [start]
BIND_TO_POST_DELIVERY = [delivered]
