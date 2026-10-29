"""Opt-in Discord confirmation reminders for successfully delivered orders."""
from __future__ import annotations

import configparser
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import requests

CONFIG_PATH = Path('configs/discord_confirmation.cfg')
DB_PATH = Path('storage/cache/discord_confirmation.sqlite3')
logger = logging.getLogger('FPC.discord_confirmation')


def settings() -> dict:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONFIG_PATH, encoding='utf-8')
    return dict(enabled=cfg.getboolean('discord','enabled',fallback=False),
                token=cfg.get('discord','token',fallback='').strip(),
                channel_id=cfg.get('discord','channel_id',fallback='').strip())


def save_settings(enabled: bool, channel_id: str, token: str = '') -> None:
    previous = settings()
    token = token.strip() or previous['token']
    channel_id = channel_id.strip()
    if channel_id and not re.fullmatch(r'[0-9]{15,22}',channel_id):
        raise ValueError('dc_invalid')
    if '\n' in token or '\r' in token or (enabled and (not token or not channel_id)):
        raise ValueError('dc_invalid')
    cfg = configparser.ConfigParser(interpolation=None)
    cfg['discord'] = dict(enabled=str(enabled),token=token,channel_id=channel_id)
    CONFIG_PATH.parent.mkdir(parents=True,exist_ok=True)
    fd, name = tempfile.mkstemp(dir=CONFIG_PATH.parent,prefix='.discord-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            cfg.write(stream)
        os.replace(name,CONFIG_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def database() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(DB_PATH,timeout=10)
    db.row_factory=sqlite3.Row
    try:
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS reminders (
                account INTEGER, order_id TEXT, delivered REAL, state TEXT,
                retry_at REAL DEFAULT 0, message_id TEXT, channel_id TEXT,
                PRIMARY KEY(account,order_id))''')
            db.execute('CREATE TABLE IF NOT EXISTS dispatch (channel_id TEXT PRIMARY KEY, next_send REAL NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS alerts (
                account INTEGER, key TEXT, code TEXT, orders TEXT, detail TEXT,
                pending INTEGER DEFAULT 1, notified REAL DEFAULT 0,
                PRIMARY KEY(account,key))''')
            yield db
    finally:
        db.close()


def record_delivery(account: int, order_id: str) -> None:
    order_id = order_id.lstrip('#')
    if not re.fullmatch(r'[A-Z0-9]{4,20}',order_id):
        return
    with database() as db:
        # Repeated callbacks must not reset the delivery time or the sent marker.
        db.execute("INSERT OR IGNORE INTO reminders(account,order_id,delivered,state) VALUES(?,?,?,'waiting')",
                   (account,order_id,time.time()))


def update(account: int, order_id: str, state: str, retry_at: float = 0,
           message_id: str | None = None) -> None:
    with database() as db:
        db.execute('UPDATE reminders SET state=?,retry_at=?,message_id=? WHERE account=? AND order_id=?',
                   (state,retry_at,message_id,account,order_id))


def queue_alert(account: int, code: str, orders: list[str], detail: str = '', repeat: bool = False) -> None:
    """Queue sanitized metadata only, with durable suppression across restarts."""
    orders=sorted(orders)
    key=code+':'+','.join(orders)
    with database() as db:
        db.execute('INSERT OR IGNORE INTO alerts(account,key,code,orders,detail) VALUES(?,?,?,?,?)',
                   (account,key,code,json.dumps(orders),detail))
        if repeat:
            db.execute('UPDATE alerts SET pending=1,detail=? WHERE account=? AND key=? AND notified<=?',
                       (detail,account,key,time.time()-3600))


def check_reminders(account: Any) -> None:
    cfg = settings()
    if not cfg['enabled'] or not cfg['token'] or not re.fullmatch(r'[0-9]{15,22}',cfg['channel_id']):
        return
    with database() as db:
        gate=db.execute('SELECT next_send FROM dispatch WHERE channel_id=?',(cfg['channel_id'],)).fetchone()
        if gate and gate['next_send']>time.time():
            return
        rows=db.execute("SELECT * FROM reminders WHERE account=? AND state='waiting' AND delivered<=? AND retry_at<=? ORDER BY delivered LIMIT 75",
                        (account.id,time.time()-86400,time.time())).fetchall()
    eligible=[]
    for row in rows:
        order_id=row['order_id']
        try:
            order=account.get_order(order_id,include_details=False)
        except Exception as exc:
            logger.warning('Confirmation order status check deferred (%s).',type(exc).__name__)
            queue_alert(account.id,'lookup',[order_id],type(exc).__name__,repeat=True)
            continue
        if order.id != order_id or order.status.name not in ('PAID','CLOSED','REFUNDED'):
            continue
        if order.status.name != 'PAID':
            update(account.id,order_id,'closed')
        else:
            eligible.append(order_id)
    if not eligible:
        return
    cfg=settings()
    if not cfg['enabled']:
        return
    claimed=[]
    with database() as db:
        # Atomically claim a single batch and its channel cooldown across workers.
        db.execute('BEGIN IMMEDIATE')
        gate=db.execute('SELECT next_send FROM dispatch WHERE channel_id=?',(cfg['channel_id'],)).fetchone()
        if gate and gate['next_send']>time.time():
            return
        for order_id in eligible:
            changed=db.execute("UPDATE reminders SET state='sending',channel_id=? WHERE account=? AND order_id=? AND state='waiting' AND retry_at<=?",
                               (cfg['channel_id'],account.id,order_id,time.time())).rowcount
            if changed:
                claimed.append(order_id)
        if not claimed:
            return
        db.execute('INSERT INTO dispatch VALUES(?,?) ON CONFLICT(channel_id) DO UPDATE SET next_send=excluded.next_send',
                   (cfg['channel_id'],time.time()+300))
    def finish(state: str, retry_at: float = 0, message_id: str | None = None) -> None:
        with database() as db:
            db.executemany('UPDATE reminders SET state=?,retry_at=?,message_id=? WHERE account=? AND order_id=?',
                           [(state,retry_at,message_id,account.id,order_id) for order_id in claimed])
    try:
        content=', '.join('#'+order_id for order_id in claimed)+' confirm please.'
        response=requests.post(
            f"https://discord.com/api/v10/channels/{cfg['channel_id']}/messages",
            headers={'Authorization':cfg['token']},
            json={'content':content,'allowed_mentions':{'parse':[]}},
            timeout=(10,20),allow_redirects=False)
        if response.status_code==429:
            try:
                delay=max(300,min(float(response.json()['retry_after']),86400))
            except (ValueError,KeyError,TypeError):
                delay=300
            retry_at=time.time()+delay
            with database() as db:
                db.execute('UPDATE dispatch SET next_send=? WHERE channel_id=?',(retry_at,cfg['channel_id']))
            finish('waiting',retry_at)
            queue_alert(account.id,'rate_limit',claimed,repeat=True)
            return
        if response.status_code in (200,201):
            data=response.json()
            message_id=str(data.get('id',''))
            if not message_id.isdigit() or str(data.get('channel_id'))!=cfg['channel_id']:
                raise ValueError('Unverified Discord receipt')
            finish('sent',message_id=message_id)
        else:
            finish('review')
            queue_alert(account.id,'send',claimed,'HTTP '+str(response.status_code))
            logger.warning('Discord confirmation needs review (HTTP %s).',response.status_code)
    except Exception as exc:
        # A lost response may have sent the whole batch. Never blindly retry it.
        finish('review')
        queue_alert(account.id,'send',claimed,type(exc).__name__)
        logger.warning('Discord confirmation outcome needs review (%s).',type(exc).__name__)
