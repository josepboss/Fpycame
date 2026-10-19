import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from admin.services import offer_stock as stock, delivery_mapping as delivery
from admin.services.offer_import import Product

URL='https://hstora.com/en/product/1358-old-title'


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        for module,name,value in [(stock,'DB_PATH',root/'stock.db'),(delivery,'API_CFG',root/'api.cfg'),
                                  (delivery,'AUTO_CFG',root/'auto.cfg'),(delivery,'PRODUCTS',root/'products')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)
        self.original=Product(1358,URL,'Old title','description','0.50',10)
        stock.save_mapping(123,1259,self.original.to_dict(),'Old title',42,markup='30')
        delivery.save_delivery_mapping('Old title','hstore','1358',True,imported=True)
        self.current=Product(1358,'https://hstora.com/en/product/1358-new-title','New title','description','0.60',10)
        p=patch.object(stock,'fetch_product',return_value=self.current);p.start();self.addCleanup(p.stop)
        self.fields=SimpleNamespace(lot_id=42,fields={'node_id':'1259'},title_en='Old title',title_ru='Old title',price=0.65,active=False)
        self.account=Mock(id=123)
        self.account.get_lot_fields.return_value=self.fields
        self.account.get_my_subcategory_lots.return_value=[]

    def test_rename_and_price_update_preserve_delivery(self):
        _,before=delivery.read_configs()
        filename=before['Old title']['productsFileName']
        (delivery.PRODUCTS/filename).write_text('existing-stock\n')
        stock.sync_metadata(self.account)
        self.assertEqual(self.fields.title_en,'New title')
        self.assertEqual(self.fields.title_ru,'New title')
        self.assertEqual(self.fields.price,0.78)
        self.assertFalse(self.fields.active)
        api,auto=delivery.read_configs()
        self.assertEqual(api['New title']['product_id'],'1358')
        self.assertIn('Old title',api)
        self.assertEqual(auto['New title']['productsFileName'],filename)
        self.assertEqual((delivery.PRODUCTS/filename).read_text(),'existing-stock\n')
        self.assertEqual(stock.mappings(123)[0]['title'],'New title')
        self.assertEqual(stock.mappings(123)[0]['markup'],'30')

    def test_deleting_old_alias_preserves_shared_inventory(self):
        from fastapi.testclient import TestClient
        from admin.main import app
        from admin.auth import get_current_user
        stock.sync_metadata(self.account)
        app.dependency_overrides[get_current_user]=lambda:'admin'
        self.addCleanup(app.dependency_overrides.clear)
        with patch('admin.routes.lots.API_DELIVERY_CFG',str(delivery.API_CFG)),patch('admin.routes.lots.AUTO_DELIVERY_CFG',str(delivery.AUTO_CFG)),patch('admin.routes.lots.validate_csrf_token',return_value=True),patch('admin.routes.lots.restart_cardinal',return_value=True),patch('admin.routes.lots.os.remove') as remove:
            response=TestClient(app,follow_redirects=False).post('/lots/delete',data={'csrf_token':'test','lot_name':'Old title'})
        self.assertEqual(response.status_code,303)
        remove.assert_not_called()
        api,auto=delivery.read_configs()
        self.assertNotIn('Old title',api)
        self.assertIn('New title',auto)

    def test_second_check_is_idempotent(self):
        stock.sync_metadata(self.account)
        self.account.reset_mock()
        stock.sync_metadata(self.account)
        self.account.save_lot.assert_not_called()

    def test_conflicting_mapping_blocks_remote_change(self):
        delivery.save_delivery_mapping('New title','hstore','999',True)
        stock.sync_metadata(self.account)
        self.account.save_lot.assert_not_called()
        self.assertEqual(stock.mappings(123)[0]['title'],'Old title')
        self.assertTrue(stock.mappings(123)[0]['last_error'])

    def test_failed_remote_save_keeps_old_baseline_and_delivery_aliases(self):
        self.account.save_lot.side_effect=TimeoutError()
        stock.sync_metadata(self.account)
        self.assertEqual(stock.mappings(123)[0]['supplier_title'],'Old title')
        api,auto=delivery.read_configs()
        self.assertIn('Old title',api);self.assertIn('New title',api)

    def test_legacy_markup_is_not_guessed_and_price_stays_unchanged(self):
        with stock.database() as db:db.execute('UPDATE mappings SET markup_confirmed=0,markup="111.63"')
        stock.sync_metadata(self.account)
        self.assertEqual(self.fields.price,0.65)
        self.assertEqual(self.fields.title_en,'New title')
        self.assertEqual(stock.mappings(123)[0]['markup_confirmed'],0)

    def test_supplier_title_over_100_is_shortened_without_losing_baseline(self):
        self.current.title='Twitter (X) Accounts 2006-2020 | Phone Verified (maybe not) | Hotmail Access | 2FA | Auth Token | 0-20 Followers'
        self.assertEqual(len(self.current.title),112)
        with stock.database() as db:db.execute("UPDATE mappings SET last_error='Metadata:ValueError'")
        stock.sync_metadata(self.account)
        self.assertLessEqual(len(self.fields.title_en),100)
        self.assertEqual(self.fields.title_en,self.current.title[:100].rstrip())
        api,auto=delivery.read_configs()
        self.assertIn(self.fields.title_en,api)
        row=stock.mappings(123)[0]
        self.assertEqual(row['supplier_title'],self.current.title)
        self.assertEqual(row['last_error'],'')
        self.account.reset_mock()
        stock.sync_metadata(self.account)
        self.account.save_lot.assert_not_called()

    def test_confirmed_markup_applies_on_next_check_without_supplier_price_change(self):
        from fastapi.testclient import TestClient
        from admin.main import app
        from admin.auth import get_current_user
        self.current.title='Old title';self.current.price='0.50'
        app.dependency_overrides[get_current_user]=lambda:'admin'
        self.addCleanup(app.dependency_overrides.clear)
        client=TestClient(app,follow_redirects=False)
        with patch('admin.routes.import_offers.validate_csrf_token',return_value=True):
            response=client.post('/imports/markup',data={'account_id':123,'node':1259,'product_id':1358,'markup':'100','csrf_token':'test'})
        self.assertEqual(response.status_code,303)
        self.assertEqual(stock.mappings(123)[0]['price_sync_pending'],1)
        stock.sync_metadata(self.account)
        self.assertEqual(self.fields.price,1.0)
        row=stock.mappings(123)[0]
        self.assertEqual(row['markup'],'100')
        self.assertEqual(row['price_sync_pending'],0)
        self.assertEqual(row['markup_confirmed'],1)

    def test_markup_edit_requires_csrf(self):
        from fastapi.testclient import TestClient
        from admin.main import app
        from admin.auth import get_current_user
        app.dependency_overrides[get_current_user]=lambda:'admin'
        self.addCleanup(app.dependency_overrides.clear)
        response=TestClient(app).post('/imports/markup',data={'account_id':123,'node':1259,'product_id':1358,'markup':'100','csrf_token':''})
        self.assertEqual(response.status_code,403)
        self.assertEqual(stock.mappings(123)[0]['markup'],'30')

    def test_refreshes_running_cardinal_config_before_remote_save(self):
        cardinal=SimpleNamespace(AD_CFG=None)
        with patch('Utils.config_loader.load_auto_delivery_config',return_value='loaded') as load:
            stock.sync_metadata(self.account,cardinal)
        self.assertEqual(cardinal.AD_CFG,'loaded')
        load.assert_called_once()

    def test_custom_import_title_preserved_until_supplier_renames(self):
        stock.save_mapping(123,1259,self.original.to_dict(),'Custom title',42,markup='30')
        self.current.title='Old title';self.current.price='0.50'
        stock.sync_metadata(self.account)
        self.account.save_lot.assert_not_called()
        self.assertEqual(stock.mappings(123)[0]['title'],'Custom title')


if __name__=='__main__':unittest.main()
