"""Confirmation requests only after a recorded successful delivery and 24h wait."""
from __future__ import annotations

import logging
import json
import time
from html import escape
from threading import Thread
from time import sleep
from typing import TYPE_CHECKING

from Utils.discord_confirmation import check_reminders, record_delivery, database, queue_alert
from locales.localizer import Localizer

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


def notify_errors(cardinal: Cardinal) -> None:
    if cardinal.telegram is None:
        return
    # Recover alerts for failed/uncertain sends, including a crash during POST.
    with database() as db:
        reviews=db.execute("SELECT order_id FROM reminders WHERE account=? AND state IN ('review','sending')",(cardinal.account.id,)).fetchall()
        alerted=set()
        for row in db.execute("SELECT orders FROM alerts WHERE account=? AND code IN ('send','review')",(cardinal.account.id,)):
            alerted.update(json.loads(row['orders']))
    for row in reviews:
        if row['order_id'] not in alerted:
            queue_alert(cardinal.account.id,'review',[row['order_id']])
    with database() as db:
        alerts=db.execute('SELECT * FROM alerts WHERE account=? AND pending=1',(cardinal.account.id,)).fetchall()
    from tg_bot import utils
    localizer=Localizer()
    for row in alerts:
        text=localizer.translate('dc_alert_title')+'\n'+localizer.translate('dc_alert_'+row['code'])
        orders=json.loads(row['orders'])
        if orders:
            text+='\n'+escape(', '.join('#'+order for order in orders))
        if row['detail']:
            text+='\n'+escape(row['detail'])
        with database() as db:
            claimed=db.execute('UPDATE alerts SET pending=0,notified=? WHERE account=? AND key=? AND pending=1',
                               (time.time(),cardinal.account.id,row['key'])).rowcount
        if claimed:
            try:
                cardinal.telegram.send_notification(text,notification_type=utils.NotificationTypes.important_announcement)
            except Exception:
                with database() as db:
                    db.execute('UPDATE alerts SET pending=1 WHERE account=? AND key=?',(cardinal.account.id,row['key']))
                raise


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
                    try:
                        queue_alert(cardinal.account.id,'monitor',[],type(exc).__name__,repeat=True)
                    except Exception:
                        logger.warning('Could not persist Discord monitor alert.')
                try:
                    notify_errors(cardinal)
                except Exception as exc:
                    logger.warning('Discord Telegram alert deferred (%s).',type(exc).__name__)
            sleep(60)
    _worker=Thread(target=loop,name='discord-confirmation',daemon=True)
    _worker.start()


BIND_TO_POST_INIT = [start]
BIND_TO_POST_DELIVERY = [delivered]
