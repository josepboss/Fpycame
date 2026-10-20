from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from admin.services import offer_stock as stock
from admin.services.offer_import import Product

URL = 'https://hstora.com/en/product/1358-twitter-2006-2018-microsoft-email-auth-token-2fa'


class StockMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        for name, value in [('DB_PATH', folder/'stock.sqlite3'), ('PUBLICATIONS_DB', folder/'imports.sqlite3')]:
            p = patch.object(stock, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.account = Mock(id=123)
        self.product = Product(1358, URL, 'Twitter', 'Description', '0.50', 0)
        self.fetch = patch.object(stock, 'fetch_live_stock', side_effect=lambda _: self.product.stock)
        self.fetch_mock = self.fetch.start()
        self.addCleanup(self.fetch.stop)
        self.fields = SimpleNamespace(lot_id=79665384, fields={'node_id':'1259','active':'on'}, active=True)
        self.account.get_lot_fields.side_effect = [self.fields,
            SimpleNamespace(fields={'node_id':'1259','active':''})]
        stock.save_mapping(123, 1259, self.product.to_dict(), 'Twitter', 79665384)

    def test_zero_stock_unchecks_and_saves_exact_linked_offer(self):
        stock.check_stock(self.account)
        self.assertFalse(self.fields.active)
        self.account.save_lot.assert_called_once_with(self.fields, locale='en')
        self.assertEqual(self.account.get_lot_fields.call_args.args[0], 79665384)
        row = stock.mappings(123)[0]
        self.assertEqual((row['last_stock'],row['state']), (0,'inactive'))

    def test_real_zero_stock_response_deactivates_offer_79779578(self):
        from admin.services.offer_import import fetch_live_stock
        stock.save_mapping(123,1259,{'product_id':1783,'url':'https://hstora.com/en/product/1783-twitter-x-accounts'},'Sold out',79779578)
        with stock.database() as db:db.execute('DELETE FROM mappings WHERE product=1358')
        self.fields.lot_id=79779578
        with patch.object(stock,'fetch_live_stock',wraps=fetch_live_stock), patch('admin.services.offer_import.requests.get') as get:
            get.return_value.status_code=200
            get.return_value.json.return_value={'success':True,'stocks':{'1783':{'stock_quantity':0,'status':'active'}}}
            stock.check_stock(self.account)
        self.account.save_lot.assert_called_once()
        self.assertFalse(self.fields.active)
        self.assertEqual(stock.mappings(123)[0]['state'],'inactive')

    def test_positive_stock_does_not_change_offer(self):
        self.product.stock = 12
        stock.check_stock(self.account)
        self.account.get_lot_fields.assert_not_called()
        self.account.save_lot.assert_not_called()
        self.assertEqual(stock.mappings(123)[0]['last_stock'], 12)

    def test_network_failure_never_means_zero_stock(self):
        self.fetch_mock.side_effect = TimeoutError()
        stock.check_stock(self.account)
        self.account.save_lot.assert_not_called()
        self.assertEqual(stock.mappings(123)[0]['last_error'], 'TimeoutError')
        self.assertIsNone(stock.mappings(123)[0]['last_stock'])

    def test_wrong_category_cannot_modify_offer(self):
        self.fields.fields['node_id'] = '777'
        stock.check_stock(self.account)
        self.account.save_lot.assert_not_called()
        self.assertTrue(stock.mappings(123)[0]['last_error'])

    def test_disabled_offer_not_saved_repeatedly(self):
        self.fields.fields['active'] = ''
        stock.check_stock(self.account)
        self.account.save_lot.assert_not_called()
        self.assertEqual(stock.mappings(123)[0]['state'],'inactive')

    def test_restock_reactivates_monitor_paused_offer(self):
        stock.check_stock(self.account)
        self.account.reset_mock()
        self.product.stock = 100
        restored = SimpleNamespace(lot_id=79665384,fields={'node_id':'1259','active':''},active=False,amount=2126)
        self.account.get_lot_fields.side_effect = [restored, SimpleNamespace(fields={'active':'on'})]
        stock.check_stock(self.account)
        self.account.save_lot.assert_called_once_with(restored,locale='en')
        self.assertTrue(restored.active)
        self.assertEqual(restored.amount,100)
        self.assertEqual(stock.mappings(123)[0]['auto_paused'],0)

    def test_manually_disabled_offer_stays_disabled_after_restock(self):
        self.fields.fields['active']=''
        stock.check_stock(self.account)
        self.assertEqual(stock.mappings(123)[0]['auto_paused'],0)
        self.product.stock=100
        self.account.reset_mock()
        stock.check_stock(self.account)
        self.account.save_lot.assert_not_called()

    def test_failed_reactivation_keeps_retry_flag(self):
        stock.check_stock(self.account)
        self.product.stock=100
        self.account.get_lot_fields.side_effect=TimeoutError()
        stock.check_stock(self.account)
        self.assertEqual(stock.mappings(123)[0]['auto_paused'],1)

    def test_account_scope(self):
        stock.check_stock(Mock(id=456))
        self.fetch_mock.assert_not_called()

    def test_pending_mapping_resolves_unique_title(self):
        stock.save_mapping(123,1259,self.product.to_dict(),'Twitter')
        self.account.get_my_subcategory_lots.return_value = [SimpleNamespace(id=79665384,description='Twitter')]
        stock.check_stock(self.account)
        self.assertEqual(stock.mappings(123)[0]['offer_id'],79665384)
        self.account.save_lot.assert_called_once()

    def test_ambiguous_title_does_not_modify_any_offer(self):
        stock.save_mapping(123,1259,self.product.to_dict(),'Twitter')
        self.account.get_my_subcategory_lots.return_value = [SimpleNamespace(id=n,description='Twitter') for n in (1,2)]
        stock.check_stock(self.account)
        self.account.save_lot.assert_not_called()

    def test_deactivation_must_be_verified(self):
        self.account.get_lot_fields.side_effect = None
        self.account.get_lot_fields.return_value = self.fields
        stock.check_stock(self.account)
        self.assertNotEqual(stock.mappings(123)[0]['state'],'inactive')
        self.assertEqual(stock.mappings(123)[0]['last_error'],'ValueError')

    def test_migration_preserves_explicit_offer_link(self):
        with closing(sqlite3.connect(stock.PUBLICATIONS_DB)) as db:
            db.execute('CREATE TABLE drafts(id TEXT, product TEXT)')
            db.execute('CREATE TABLE publications(account INT,node INT,product INT,draft TEXT,state TEXT)')
            db.execute('INSERT INTO drafts VALUES (?,?)',('draft',json.dumps(self.product.to_dict())))
            db.execute('INSERT INTO publications VALUES (123,1259,1358,?,?)',('draft','published'))
            db.commit()
        stock.migrate_publications(123)
        self.assertEqual(stock.mappings(123)[0]['offer_id'],79665384)
        with stock.database() as db: db.execute('DELETE FROM mappings')
        stock.migrate_publications(123)
        self.assertIsNone(stock.mappings(123)[0]['offer_id'])

    def test_removal_states_stop_monitor_actions(self):
        for state in ['removing','removal_error','removed']:
            with stock.database() as db:db.execute('UPDATE mappings SET state=?',(state,))
            stock.check_stock(self.account)
            stock.sync_metadata(self.account)
        self.fetch_mock.assert_not_called()
        self.account.save_lot.assert_not_called()

    def test_response_identifiers(self):
        self.assertEqual(stock.response_offer_id({'offer_id':79665384}),79665384)
        self.assertEqual(stock.response_offer_id({'url':'/en/lots/offerEdit?node=1259&offer=79665384'}),79665384)
        self.assertIsNone(stock.response_offer_id({'url':'/en/lots/1259/trade'}))
        self.assertIsNone(stock.response_offer_id({'url':'https://evil.test/lots/offer?id=123'}))


if __name__ == '__main__': unittest.main()
