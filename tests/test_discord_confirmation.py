import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import patch, Mock

import requests
from fastapi.testclient import TestClient
from admin.auth import get_current_user
from admin.main import app
from Utils import discord_confirmation as dc
from plugins import discord_confirmation as plugin

CHANNEL='123456789012345678'


class DiscordConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(dc,'DB_PATH',Path(self.tmp.name)/'reminders.db'),
                      patch.object(dc,'CONFIG_PATH',Path(self.tmp.name)/'discord.cfg')]
        for p in self.patches:p.start()
        dc.save_settings(True,CHANNEL,'private-test-token')
        self.account=Obj(id=7,get_order=Mock(return_value=Obj(id='BAZC7KM2',status=Obj(name='PAID'))))
    def tearDown(self):
        for p in self.patches:p.stop()
        self.tmp.cleanup()
    def delivered(self,ago=86401):
        with patch.object(dc.time,'time',return_value=100000-ago):dc.record_delivery(7,'BAZC7KM2')
    def check(self,post):
        with patch.object(dc.time,'time',return_value=100000),patch.object(dc.time,'sleep'),patch.object(dc.requests,'post',post):
            dc.check_reminders(self.account)
    def state(self):
        with dc.database() as db:return db.execute('SELECT * FROM reminders').fetchone()
    def test_success_only_once_after_24h_and_no_mentions(self):
        self.delivered(86399)
        post=Mock(return_value=Obj(status_code=200,json=lambda:dict(id='123',channel_id=CHANNEL)))
        self.check(post)
        post.assert_not_called()
        with dc.database() as db:db.execute('UPDATE reminders SET delivered=1')
        self.check(post)
        self.check(post)
        self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.kwargs['json'],{'content':'#BAZC7KM2 confirm please.','allowed_mentions':{'parse':[]}})
        self.assertEqual(self.state()['state'],'sent')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        dc.record_delivery(7,'BAZC7KM2')
        self.assertEqual(self.state()['state'],'sent')
    def test_disabled_and_closed_never_post(self):
        self.delivered()
        post=Mock()
        dc.save_settings(False,CHANNEL)
        self.check(post)
        self.account.get_order.assert_not_called()
        dc.save_settings(True,CHANNEL)
        self.account.get_order.return_value.status.name='CLOSED'
        self.check(post)
        post.assert_not_called()
        self.assertEqual(self.state()['state'],'closed')
    def test_uncertain_timeout_never_retries(self):
        self.delivered()
        post=Mock(side_effect=requests.Timeout())
        self.check(post)
        self.check(post)
        self.assertEqual(post.call_count,1)
        self.assertEqual(self.state()['state'],'review')
    def test_rate_limit_is_deferred(self):
        self.delivered()
        post=Mock(return_value=Obj(status_code=429,json=lambda:dict(retry_after=120)))
        self.check(post)
        self.check(post)
        self.assertEqual(post.call_count,1)
        self.assertEqual(self.state()['retry_at'],100300)

    def test_multiple_orders_share_one_message_and_receipt(self):
        self.delivered()
        with patch.object(dc.time,'time',return_value=1):
            dc.record_delivery(7,'SECOND12')
        self.account.get_order.side_effect=lambda order_id,**kw:Obj(id=order_id,status=Obj(name='PAID'))
        post=Mock(return_value=Obj(status_code=200,json=lambda:dict(id='123',channel_id=CHANNEL)))
        self.check(post)
        post.assert_called_once()
        content=post.call_args.kwargs['json']['content']
        self.assertIn('#BAZC7KM2',content)
        self.assertIn('#SECOND12',content)
        self.assertEqual(content.count('confirm please.'),1)
        with dc.database() as db:
            rows=db.execute('SELECT state,message_id FROM reminders').fetchall()
        self.assertTrue(all(r['state']=='sent' and r['message_id']=='123' for r in rows))
        with patch.object(dc.time,'time',return_value=1):dc.record_delivery(7,'THIRD123')
        self.check(post)
        self.assertEqual(post.call_count,1)
    def test_fresh_funpay_lookup_failure_does_not_post(self):
        self.delivered()
        self.account.get_order.side_effect=TimeoutError()
        post=Mock()
        self.check(post)
        post.assert_not_called()
        self.assertEqual(self.state()['state'],'waiting')
    def test_delivery_hook_excludes_failures_and_smm_placeholders(self):
        c=Obj(account=Obj(id=7))
        event=Obj(delivered=False,error=False,order=Obj(id='BAZC7KM2'),config_section_name='Example')
        with patch.object(plugin,'record_delivery') as record,patch('plugins.api_delivery.get_delivery_config',return_value={'Example':{'provider':'smmcost'}}):
            plugin.delivered(c,event)
            event.delivered=True
            plugin.delivered(c,event)
            record.assert_not_called()
        with patch.object(plugin,'record_delivery') as record,patch('plugins.api_delivery.get_delivery_config',return_value={}):
            plugin.delivered(c,event)
            record.assert_called_once_with(7,'BAZC7KM2')
    def test_saved_token_not_reflected_and_csrf_required(self):
        app.dependency_overrides[get_current_user]=lambda:'test'
        try:
            with patch('admin.routes.settings.get_csrf_token',return_value='csrf'),patch('admin.routes.settings.validate_csrf_token',return_value=False):
                client=TestClient(app)
                response=client.get('/settings/')
                self.assertEqual(response.status_code,200)
                self.assertNotIn('private-test-token',response.text)
                self.assertEqual(client.post('/settings/save_discord',data={'csrf_token':'bad'}).status_code,403)
        finally:app.dependency_overrides.clear()
        dc.save_settings(False,CHANNEL,'')
        self.assertEqual(dc.settings()['token'],'private-test-token')
        self.assertEqual(dc.CONFIG_PATH.stat().st_mode & 0o777,0o600)

    def test_error_alert_sent_to_telegram_only_once_and_without_token(self):
        self.delivered()
        self.check(Mock(side_effect=requests.Timeout()))
        cardinal=Obj(account=self.account,telegram=Obj(send_notification=Mock()))
        plugin.notify_errors(cardinal)
        plugin.notify_errors(cardinal)
        cardinal.telegram.send_notification.assert_called_once()
        text=cardinal.telegram.send_notification.call_args.args[0]
        self.assertIn('#BAZC7KM2',text)
        self.assertNotIn('private-test-token',text)

    def test_lookup_error_dedup_and_alert_waits_for_telegram(self):
        dc.queue_alert(7,'lookup',['BAZC7KM2'],'TimeoutError',repeat=True)
        plugin.notify_errors(Obj(account=self.account,telegram=None))
        cardinal=Obj(account=self.account,telegram=Obj(send_notification=Mock()))
        plugin.notify_errors(cardinal)
        dc.queue_alert(7,'lookup',['BAZC7KM2'],'TimeoutError',repeat=True)
        plugin.notify_errors(cardinal)
        cardinal.telegram.send_notification.assert_called_once()
