import unittest
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch
import requests
import announcements
from tg_bot.bot import TGBot
from tg_bot.utils import NotificationTypes
from FunPayAPI.common.exceptions import RequestFailedError


class HardeningTests(unittest.TestCase):
    def test_unauthorized_message_cannot_register_notifications(self):
        bot=TGBot.__new__(TGBot)
        bot.authorized_users={7:{}}
        bot.notification_settings={}
        message=Obj(chat=Obj(id=9),from_user=Obj(id=9))
        with patch('tg_bot.utils.save_notification_settings') as save:
            bot.setup_chat_notifications(bot,message)
            save.assert_not_called()
        self.assertEqual(bot.notification_settings,{})
    def test_important_alert_does_not_go_to_revoked_private_user(self):
        bot=TGBot.__new__(TGBot)
        bot.authorized_users={7:{}}
        bot.notification_settings={'7':{},'9':{}}
        bot.bot=Obj(send_message=Mock())
        bot.send_notification('Private order report',notification_type=NotificationTypes.important_announcement)
        bot.bot.send_message.assert_called_once_with('7','Private order report')
    def test_remote_announcements_never_fetch_or_send(self):
        with patch('requests.get') as get:
            self.assertIsNone(announcements.get_announcement(True))
            announcements.main(Mock())
            get.assert_not_called()
    def test_error_logs_redact_request_and_response_without_mutating_headers(self):
        response=requests.Response();response.status_code=403
        response.request=requests.Request('POST','https://funpay.com/runner/?token=secret-query',headers={'Cookie':'secret-cookie','Authorization':'secret-auth'},data='secret-body').prepare()
        response._content=b'secret-response'
        exc=RequestFailedError(response);exc.log_response=True
        text=str(exc)+exc.short_str()
        for value in ['secret-query','secret-cookie','secret-auth','secret-body','secret-response']:
            self.assertNotIn(value,text)
        self.assertIn('403',text)
        self.assertEqual(response.request.headers['Cookie'],'secret-cookie')
    def test_bot_profile_keeps_owner_name_and_has_no_promotional_links(self):
        bot=TGBot.__new__(TGBot);bot.bot=Mock()
        bot.edit_bot()
        bot.bot.set_my_name.assert_not_called()
        for call in bot.bot.set_my_description.call_args_list:
            self.assertNotIn('sidor',call.args[0])
            self.assertNotIn('t.me',call.args[0])
