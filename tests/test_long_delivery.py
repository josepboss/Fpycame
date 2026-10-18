import configparser
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Utils.delivery_tools import format_products
from locales.localizer import Localizer


def translate(key, *args):
    return Localizer().translate(key, *args, language='en')


def reconstruct(text):
    result=[]
    parts=[]
    for line in text.splitlines():
        if re.fullmatch(r'Product \d+',line):
            if parts: result.append(''.join(parts))
            parts=[]
        match=re.fullmatch(r'Part \d+/\d+: \[(.*)\]',line)
        if match:parts.append(match[1])
    if parts:result.append(''.join(parts))
    return result


class LongDeliveryTests(unittest.TestCase):
    def test_original_long_tokens_and_record_delimiters_survive(self):
        products=['user:pass:email|'+('REFRESH_'*100)+'|client:ct0:2fa:token',
                  'user2:'+('TOKEN_'*90)]
        text,chunked=format_products(products,translate)
        self.assertTrue(chunked)
        self.assertEqual(reconstruct(text),products)
        self.assertLessEqual(max(map(len,re.findall(r'\S+',text))),80)

    def test_punctuation_and_whitespace_inside_chunks_survive(self):
        record='  '+('X'*61)+' [a]: | '+('Y'*100)+'  '
        text,_=format_products([record],translate)
        self.assertEqual(reconstruct(text),[record])

    def test_short_credentials_are_unchanged(self):
        products=['user:pass','second:pass']
        self.assertEqual(format_products(products,translate),('user:pass\nsecond:pass',False))

    def test_real_cardinal_dispatch_sends_literal_chunks_and_roundtrips(self):
        from cardinal import Cardinal
        c=object.__new__(Cardinal)
        c.MAIN_CFG=configparser.ConfigParser()
        c.MAIN_CFG['Other']={'watermark':''}
        c.MAIN_CFG['FunPay']={'oldMsgGetMode':'0','keepSentMessagesUnread':'0'}
        c.account=Mock()
        record='user:pass:'+('A'*100)+'$photo=12345'+('B'*1800)+'$sleep=10'
        text,_=format_products([record],translate)
        self.assertTrue(c.send_message(123,text,watermark=False,literal=True))
        sent=[call.args[1] for call in c.account.send_message.call_args_list]
        self.assertGreater(len(sent),1)
        self.assertEqual(reconstruct('\n'.join(sent)),[record])
        self.assertTrue(all(len(message.splitlines())<=20 for message in sent))
        self.assertTrue(all(max(map(len,re.findall(r'\S+',message)))<=80 for message in sent))
        c.account.send_image.assert_not_called()

    def test_order_delivery_uses_literal_lossless_path(self):
        from cardinal import Cardinal
        from handlers import deliver_goods
        c=object.__new__(Cardinal)
        c.MAIN_CFG=configparser.ConfigParser()
        c.MAIN_CFG['Other']={'watermark':''}
        c.MAIN_CFG['FunPay']={'oldMsgGetMode':'0','keepSentMessagesUnread':'0','multiDelivery':'1'}
        c.account=Mock()
        c.account.get_chat_by_name.return_value=SimpleNamespace(id=123)
        cfg=configparser.ConfigParser(interpolation=None)
        cfg['Lot']={'response':'Your product: $product','productsFileName':'test.txt'}
        order=SimpleNamespace(buyer_username='demo',chat_id=123,amount=1,id='TEST')
        event=SimpleNamespace(order=order,config_section_obj=cfg['Lot'])
        record='demo:'+('REFRESH_TOKEN'*50)
        with patch('handlers._',side_effect=translate),patch('handlers.cardinal_tools.get_products',return_value=([record],0)),patch('handlers.cardinal_tools.format_order_text',return_value='Your product:\n$product'):
            deliver_goods(c,event)
        self.assertTrue(event.delivered)
        sent='\n'.join(call.args[1] for call in c.account.send_message.call_args_list)
        self.assertEqual(reconstruct(sent),[record])

    def test_hstore_stock_retains_complete_original_data(self):
        import plugins.api_delivery as plugin
        with tempfile.TemporaryDirectory() as folder:
            original='user:pass|'+('refresh-token-'*100)+'|client:ct0:token'
            cfg=configparser.ConfigParser()
            cfg['Lot']={'provider':'hstore','product_id':'1358'}
            ad=configparser.ConfigParser()
            ad['Lot']={'productsFileName':'delivery.txt'}
            event=SimpleNamespace(config_section_name='Lot',order=SimpleNamespace(id='TEST',amount=1))
            response=Mock(status_code=200)
            response.json.return_value={'data':{'delivery':{'items':[original]}}}
            # The plugin uses a relative stock directory; intercept only its open.
            target=Path(folder)/'delivery.txt'
            builtin_open=open
            def redirected(path,*args,**kwargs):
                return builtin_open(target if str(path)=='storage/products/delivery.txt' else path,*args,**kwargs)
            with patch.object(plugin,'get_delivery_config',return_value=cfg),patch.object(plugin,'hstore_request',return_value=response),patch('builtins.open',side_effect=redirected):
                self.assertTrue(plugin.handle_hstore(SimpleNamespace(AD_CFG=ad),event))
            self.assertEqual(target.read_text(),original+'\n')


if __name__=='__main__':unittest.main()
