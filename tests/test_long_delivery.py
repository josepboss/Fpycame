import configparser
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Utils.delivery_tools import format_products, account_fields, format_spec, remove_refresh_hints
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

    def test_known_account_format_omits_only_refresh_token(self):
        token='FAKE_REFRESH_'*30
        record=f'demo_user----demo_pass----demo@example.test|mail_pass|{token}|client_demo----2FA_DEMO----auth_demo'
        text,literal=format_products([record],translate)
        self.assertTrue(literal)
        self.assertIn('Username: demo_user',text)
        self.assertIn('Password: demo_pass',text)
        self.assertIn('Email: demo@example.test',text)
        self.assertIn('Email password: mail_pass',text)
        self.assertIn('2FA secret: 2FA_DEMO',text)
        self.assertIn('Auth token: auth_demo',text)
        self.assertIn('Email client ID: client_demo',text)
        chunks=re.findall(r'^Product 1 / Email refresh token \d+/\d+: \[(.*)\]$',text,re.M)
        self.assertEqual(chunks,[])
        self.assertNotIn('Email refresh token',text)
        self.assertNotIn('FAKE_REFRESH_',text)
        self.assertLessEqual(max(map(len,re.findall(r'\S+',text))),80)
        self.assertNotIn('Part 1/',text)

    def test_structured_parser_roundtrips_original_format(self):
        original='user----pass----mail@example.test|mailpass|refresh|client----2fa----auth'
        values=dict(account_fields(original))
        reconstructed='----'.join([values['delivery_field_username'],values['delivery_field_password'],
            '|'.join(values[k] for k in ('delivery_field_email','delivery_field_email_password','delivery_field_refresh','delivery_field_client')),
            values['delivery_field_2fa'],values['delivery_field_auth']])
        self.assertEqual(reconstructed,original)

    def test_unknown_or_ambiguous_formats_are_never_guessed(self):
        for record in ['user----pass----mail@example.test|pass|token----2fa----auth',
                       'user----pass----not-an-email|pass|refresh|client----2fa----auth',
                       'user----pass----mail@example.test|pass|refresh|client----2fa----auth----extra']:
            self.assertIsNone(account_fields(record))

    def test_three_accounts_omit_refresh_tokens_and_keep_account_fields(self):
        products=[f'user{n}----pass{n}----mail{n}@example.test|mailpass|'+('FAKE_REFRESH_'*40)+f'|client----2fa{n}----auth{n}' for n in range(1,4)]
        text,_=format_products(products,translate)
        for n in range(1,4):
            self.assertIn(f'Product {n}',text)
            self.assertIn(f'Username: user{n}',text)
        self.assertNotIn('FAKE_REFRESH_',text)
        self.assertEqual(text.count('Username:'),3)

    def test_long_auth_token_is_preserved_and_split_instead_of_removed(self):
        auth='FAKE_AUTH_'*30
        record=f'user----pass----mail@example.test|mailpass|OMIT_REFRESH|client----2fa----{auth}'
        text,_=format_products([record],translate)
        chunks=re.findall(r'^Product 1 / Auth token \d+/\d+: \[(.*)\]$',text,re.M)
        self.assertEqual(''.join(chunks),auth)
        self.assertNotIn('OMIT_REFRESH',text)

    def test_optional_schema_supports_different_field_order_and_separators(self):
        text,_=format_products(['mail@example.test:secret:user:REFRESH_WITHOUT_PREFIX:auth'],translate,
                              'email:password:username:refresh_token:auth_token')
        self.assertIn('Username: user',text)
        self.assertIn('Email: mail@example.test',text)
        self.assertIn('Password: secret',text)
        self.assertIn('Auth token: auth',text)
        self.assertNotIn('REFRESH_WITHOUT_PREFIX',text)

    def test_configured_password_with_mc_prefix_is_not_removed(self):
        password='M.C'+'X'*120
        text,_=format_products(['user|'+password+'|auth'],translate,'username|password|auth_token')
        chunks=re.findall(r'^Product 1 / Password \d+/\d+: \[(.*)\]$',text,re.M)
        self.assertEqual(''.join(chunks),password)

    def test_missing_schema_removes_only_long_complete_mc_fields(self):
        token='M.C'+'X'*120
        for separator in ['|',':','----',';',' ']:
            record=separator.join(['user','password',token,'auth'])
            with self.subTest(separator=separator):
                self.assertEqual(remove_refresh_hints(record),separator.join(['user','password','','auth']))
        self.assertEqual(remove_refresh_hints(token),'')
        self.assertEqual(remove_refresh_hints('M.C'+'X'*96),'M.C'+'X'*96)
        self.assertEqual(remove_refresh_hints('auth|'+'Z'*200),'auth|'+'Z'*200)
        self.assertEqual(remove_refresh_hints('prefixM.C'+'X'*120),'prefixM.C'+'X'*120)

    def test_schema_mismatch_falls_back_without_wrong_labels(self):
        text,_=format_products(['user|password|M.C'+'X'*120+'|auth'],translate,'username:password:auth_token')
        self.assertNotIn('Username:',text)
        self.assertNotIn('M.C',text)
        self.assertIn('password',text)

    def test_schema_validation_rejects_unknown_duplicate_and_unsafe_templates(self):
        for spec in ['username|unknown','username|username','refresh_token','username<password','username\npassword']:
            with self.subTest(spec=spec),self.assertRaises(ValueError):format_spec(spec)

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
