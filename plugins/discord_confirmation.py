"""Confirmation requests only after a recorded successful delivery and 24h wait."""
from __future__ import annotations

import logging
import json
import time
import hashlib
from html import escape
from threading import Thread, Lock
from time import sleep
from typing import TYPE_CHECKING

from Utils.discord_confirmation import check_reminders, record_delivery, database, queue_alert, backup_orders, settings
from locales.localizer import Localizer

if TYPE_CHECKING:
    from cardinal import Cardinal
    from FunPayAPI.updater.events import NewOrderEvent

NAME = 'Discord Confirmation Reminders'
VERSION = '1.1.0'
DESCRIPTION = 'Request confirmation once for orders unconfirmed 24h after successful delivery.'
CREDITS = 'Cardinal'
UUID = '310d7af6-6764-4ea9-a71a-d051b83e7c0a'
SETTINGS_PAGE = False
BIND_TO_DELETE = None
logger = logging.getLogger('FPC.discord_confirmation')
_worker: Thread | None = None
_cycle_lock = Lock()


def backup_messages(report: dict) -> list[str]:
    tr=Localizer().translate
    messages=[tr('dc_backup_title')+'\n'+tr('dc_backup_note')]
    for offset in range(0,len(report['ready']),75):
        content=', '.join('#'+order for order in report['ready'][offset:offset+75])+' confirm please.'
        messages.append(tr('dc_backup_ready')+'\n<code>'+escape(content)+'</code>')
    if not report['ready']:
        messages.append(tr('dc_backup_empty'))
    for offset in range(0,len(report['held']),30):
        lines=[]
        for row in report['held'][offset:offset+30]:
            reason=row['hold_reason'] or ('send_review' if row['state'] in ('review','sending') else 'unchecked')
            lines.append(escape('#'+row['order_id'])+' — '+tr('dc_hold_'+reason))
        messages.append(tr('dc_backup_held')+'\n'+'\n'.join(lines))
    return messages


def send_backup(cardinal: Cardinal, force: bool = False, chat_id: int | None = None) -> None:
    if cardinal.telegram is None:
        return
    report=backup_orders(cardinal.account)
    if not force and not report['ready'] and not report['held']:
        return
    # With automation enabled, a backup is needed only for holds/send issues.
    cfg=settings()
    automatic=cfg['enabled'] and bool(cfg['token']) and bool(cfg['channel_id'])
    if not force and automatic and not report['held']:
        return
    digest=hashlib.sha256(json.dumps(report,sort_keys=True).encode()).hexdigest()
    with database() as db:
        previous=db.execute('SELECT * FROM backups WHERE account=?',(cardinal.account.id,)).fetchone()
        if not force and previous and previous['digest']==digest and previous['notified']>time.time()-86400:
            return
    from tg_bot import utils
    for text in backup_messages(report):
        if chat_id is not None:
            cardinal.telegram.bot.send_message(chat_id,text)
        else:
            cardinal.telegram.send_notification(text,notification_type=utils.NotificationTypes.important_announcement)
    if not force:
        with database() as db:
            db.execute('INSERT INTO backups VALUES(?,?,?) ON CONFLICT(account) DO UPDATE SET digest=excluded.digest,notified=excluded.notified',
                       (cardinal.account.id,digest,time.time()))


def register_commands(cardinal: Cardinal) -> None:
    if cardinal.telegram is None:
        return
    def command(message) -> None:
        if (message.from_user.id not in cardinal.telegram.authorized_users or
                str(message.chat.id) not in cardinal.telegram.notification_settings):
            return
        def run() -> None:
            with _cycle_lock:
                try:
                    send_backup(cardinal,force=True,chat_id=message.chat.id)
                except Exception as exc:
                    logger.warning('Confirmation backup deferred (%s).',type(exc).__name__)
                    cardinal.telegram.bot.send_message(message.chat.id,Localizer().translate('dc_backup_failed'))
        Thread(target=run,name='confirmation-backup',daemon=True).start()
    cardinal.telegram.msg_handler(command,commands=['confirmation_list'])
    cardinal.add_telegram_commands(UUID,[('confirmation_list',Localizer().translate('dc_backup_command'),True)])


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
                with _cycle_lock:
                    try:
                        check_reminders(cardinal.account)
                        send_backup(cardinal)
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
BIND_TO_PRE_INIT = [register_commands]
BIND_TO_POST_DELIVERY = [delivered]
