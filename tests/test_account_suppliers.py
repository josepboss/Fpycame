import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from admin.services import offer_stock as stock
from admin.services import delivery_mapping as delivery
from admin.services import offer_import as imports
from plugins.providers import accounts, fulfillment


class SupplierTests(unittest.TestCase):
    def setUp(self):
        translation=patch('admin.services.offer_translation.translate_text',side_effect=lambda text,source,target:text)
        translation.start();self.addCleanup(translation.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for module, name, value in [(stock,'DB_PATH',self.folder/'stock.db'),
            (fulfillment,'DB_PATH',self.folder/'orders.db'),
            (delivery,'API_CFG',self.folder/'api.cfg'),(delivery,'AUTO_CFG',self.folder/'auto.cfg'),
            (delivery,'PRODUCTS',self.folder/'products')]:
            p=patch.object(module,name,value);p.start();self.addCleanup(p.stop)

    def product(self, provider='djekxa', stock_count=0):
        return imports.Product(1358,'https://djekxa.ru/product/1358' if provider=='djekxa' else 'https://dark.shopping/product/1358',
                               provider+' title','description','50',stock_count,'RUB',provider)

    def test_import_djekxa_schema_and_currency(self):
        data={'id':1358,'title':'Demo','description':'<p>Hello<br>World</p>','price':50,'quantity':3}
        with patch.object(accounts.AccountSupplier,'product',return_value=data):
            p=imports.fetch_product('https://djekxa.ru/product/1358')
        self.assertEqual((p.provider,p.currency,p.stock,p.price),('djekxa','RUB',3,'50'))
        self.assertIn('Hello\nWorld',p.description)
        self.assertEqual(imports.selling_price(imports.usd_cost(p.price,'RUB','100'),'50'),'0.75')
        with self.assertRaises(imports.ImportFailure):imports.usd_cost('50','RUB','')

    def test_darkstore_slug_resolves_only_main_product_card(self):
        response=Mock(status_code=200,text='<div class="card-product" data-product-id="1358"></div><div data-product-id="99"></div>')
        data={'id':1358,'name':'Demo','description':'Test','manual':'Instructions','price':20,'quantity':0,'minimum_order':2}
        with patch.object(imports.requests,'get',return_value=response),patch.object(accounts.AccountSupplier,'product',return_value=data):
            p=imports.fetch_product('https://dark.shopping/product/demo-product')
        self.assertEqual((p.provider,p.product_id,p.minimum_order,p.stock),('dark',1358,2,0))
        self.assertIn('Instructions',p.description)

    def test_manual_products_and_unknown_hosts_rejected(self):
        with patch.object(accounts.AccountSupplier,'product',return_value={'id':1358,'is_manual_order_delivery':1}):
            with self.assertRaises(imports.ImportFailure):imports.fetch_product('https://dark.shopping/product/1358')
        for url in ['https://evil.test/product/1358','https://djekxa.ru@evil.test/product/1358',
                    'http://djekxa.ru/product/1358','https://djekxa.ru/product/1358?key=secret']:
            with self.assertRaises(imports.ImportFailure):imports.fetch_product(url)

    def test_same_product_id_keeps_three_independent_mappings(self):
        h=imports.Product(1358,'https://hstora.com/en/product/1358-demo','HStore','Demo','1',2)
        for p in (h,self.product(),self.product('dark')):
            stock.save_mapping(7,1259,p.to_dict(),p.title,111,markup='50',rub_per_usd='100')
        rows=stock.mappings(7)
        self.assertEqual(len(rows),3)
        self.assertEqual({r['provider'] for r in rows},{'hstore','dark','djekxa'})

    def test_existing_sqlite_mapping_is_migrated_without_losing_state(self):
        db=sqlite3.connect(stock.DB_PATH)
        db.execute('''CREATE TABLE mappings(account INTEGER,node INTEGER,product INTEGER,source_url TEXT,
            title TEXT,offer_id INTEGER,last_stock INTEGER,checked REAL,state TEXT NOT NULL DEFAULT 'pending',last_error TEXT,
            PRIMARY KEY(account,node,product))''')
        db.execute("INSERT INTO mappings VALUES (7,1259,1358,'https://hstora.com/en/product/1358-demo','Old',111,0,1,'inactive','')")
        db.commit();db.close()
        self.assertEqual(stock.mappings()[0]['provider'],'hstore')
        self.assertEqual(stock.mappings()[0]['state'],'inactive')
        stock.save_mapping(7,1259,self.product().to_dict(),'New',222)
        self.assertEqual(len(stock.mappings()),2)

    def test_provider_stock_pauses_and_restores_only_its_offer(self):
        for provider in ('dark','djekxa'):
            p=self.product(provider)
            stock.save_mapping(7,1259,p.to_dict(),p.title,111)
            active=SimpleNamespace(lot_id=111,fields={'node_id':'1259','active':'on'},amount=2)
            inactive=SimpleNamespace(lot_id=111,fields={'node_id':'1259','active':''},amount=2)
            account=Mock(id=7)
            account.get_lot_fields.side_effect=[active,inactive]
            with patch.object(stock,'fetch_live_stock',return_value=p.stock):stock.check_stock(account)
            self.assertFalse(active.active)
            p.stock=5
            restored=SimpleNamespace(fields={'active':'on'})
            account.get_lot_fields.side_effect=[inactive,restored]
            with patch.object(stock,'fetch_live_stock',return_value=p.stock):stock.check_stock(account)
            self.assertTrue(inactive.active)
            with stock.database() as db:db.execute('DELETE FROM mappings')

    def test_rename_and_removal_do_not_cross_provider(self):
        delivery.save_delivery_mapping('Djek','djekxa','1358',True,imported=True,delivery_format='username|password')
        delivery.save_delivery_mapping('Dark','dark','1358',True,imported=True)
        delivery.alias_delivery_mapping('Djek','Renamed',1358,'djekxa')
        api,auto=delivery.read_configs()
        self.assertEqual(api['Renamed']['provider'],'djekxa')
        self.assertEqual(auto['Renamed']['deliveryFormat'],'username|password')
        delivery.remove_delivery_mappings('Renamed',1358,set(),'djekxa')
        api,auto=delivery.read_configs()
        self.assertNotIn('Djek',api)
        self.assertIn('Dark',api)

    def client(self):
        client=Mock(key='synthetic-key')
        client.create_order.return_value='supplier-1'
        client.order.return_value={'status':'completed'}
        client.items.return_value=['user|password']
        return client

    def test_pending_polls_to_completion_without_another_purchase(self):
        client=self.client()
        client.order.side_effect=[{'status':'in-progress'},{'status':'completed'}]
        with patch.object(fulfillment,'AccountSupplier',return_value=client),patch.object(fulfillment.time,'sleep'):
            self.assertEqual(fulfillment.obtain(7,'TEST','djekxa',1358,1),['user|password'])
            with self.assertRaises(accounts.SupplierError):fulfillment.obtain(7,'TEST','djekxa',1358,1)
        client.create_order.assert_called_once()
        self.assertEqual(client.order.call_count,2)

    def test_timeout_resumes_saved_order_without_repurchase(self):
        client=self.client()
        with patch.object(fulfillment,'AccountSupplier',return_value=client):
            with self.assertRaises(accounts.SupplierError):fulfillment.obtain(7,'TEST','dark',1358,1,max_wait=0)
            self.assertEqual(fulfillment.obtain(7,'TEST','dark',1358,1),['user|password'])
        client.create_order.assert_called_once()

    def test_lost_creation_response_never_retries_purchase(self):
        client=self.client();client.create_order.side_effect=accounts.SupplierError('timeout')
        with patch.object(fulfillment,'AccountSupplier',return_value=client):
            for _ in range(2):
                with self.assertRaises(accounts.SupplierError):fulfillment.obtain(7,'TEST','djekxa',1358,1)
        client.create_order.assert_called_once()

    def test_partial_delivery_stops_for_review(self):
        client=self.client();client.order.return_value={'status':'partially-completed'}
        with patch.object(fulfillment,'AccountSupplier',return_value=client):
            with self.assertRaises(accounts.SupplierError):fulfillment.obtain(7,'TEST','djekxa',1358,2)
        client.items.assert_not_called()

    def test_download_preserves_credentials_and_rejects_wrong_count(self):
        with patch.object(accounts,'get_credentials',return_value=[{'api_key':'synthetic'}]):
            client=accounts.AccountSupplier('djekxa')
        data={'status':'completed','items':[{'product_id':1358,'link_to_file':'https://cdn.djekxa.biz/sold/demo.txt'}]}
        response=Mock(status_code=200,headers={'Content-Type':'text/plain'},content=b'  user|password  \r\n')
        with patch.object(accounts.requests,'get',return_value=response):
            self.assertEqual(client.items('supplier-1',data,1358,1),['  user|password  '])
            with self.assertRaises(accounts.SupplierError):client.items('supplier-1',data,1358,2)
        with self.assertRaises(accounts.SupplierError):client.download('https://localhost/private')

    def test_provider_card_key_save_preserves_other_keys_and_smm(self):
        from fastapi.testclient import TestClient
        from admin.main import app
        from admin.auth import get_current_user
        cfg=self.folder/'providers.json'
        cfg.write_text(json.dumps({'hstore':{'credentials':[{'api_key':'existing-h','api_secret':'existing-secret'}]}}))
        keys=self.folder/'keys.cfg';keys.write_text('[hstore]\napi_key=existing-h\napi_secret=existing-secret\n[smmcost]\napi_key=existing-smm\n')
        app.dependency_overrides[get_current_user]=lambda:'test-admin'
        try:
            with patch('admin.routes.providers.PROVIDERS_CFG',str(cfg)),patch('admin.routes.providers.API_KEYS_CFG',str(keys)):
                response=TestClient(app).post('/providers/save_api_keys',data={'djekxa_key':'new-djekxa'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            data=json.loads(cfg.read_text())
            self.assertEqual(data['hstore']['credentials'][0]['api_key'],'existing-h')
            self.assertEqual(data['djekxa']['credentials'][0]['api_key'],'new-djekxa')
            self.assertIn('existing-smm',keys.read_text())
        finally:
            app.dependency_overrides.clear()

    def test_api_purchase_payloads_use_verified_endpoints_and_no_redirects(self):
        for name in ('dark','djekxa'):
            with patch.object(accounts,'get_credentials',return_value=[{'api_key':'synthetic'}]):
                client=accounts.AccountSupplier(name)
            response=Mock(status_code=201)
            response.json.return_value={'success':True,'data':{'id':42,'uuid':'demo-uuid'}}
            with patch.object(accounts.requests,'request',return_value=response) as request,patch.object(accounts.time,'sleep'):
                client.create_order(1358,1,'TEST')
            call=request.call_args
            self.assertEqual(call.args[0],'POST')
            self.assertFalse(call.kwargs['allow_redirects'])
            if name=='dark':
                self.assertIn('/api/v1/order/create',call.args[1])
                self.assertEqual(call.kwargs['data']['idempotence_id'],'funpay-TEST')
            else:
                self.assertIn('/api/v2/orders',call.args[1])
                self.assertEqual(call.kwargs['json']['items'],[{'product_id':1358,'quantity':1}])

    def test_wrong_delivery_count_is_saved_for_review_without_repurchase(self):
        client=self.client()
        client.items.side_effect=accounts.SupplierReview('Wrong count')
        with patch.object(fulfillment,'AccountSupplier',return_value=client):
            for _ in range(2):
                with self.assertRaises(accounts.SupplierError):fulfillment.obtain(7,'TEST','djekxa',1358,2)
        client.create_order.assert_called_once()
        with fulfillment.database() as db:
            self.assertEqual(db.execute('SELECT state FROM orders').fetchone()[0],'review')

    def test_background_worker_does_not_block_and_deduplicates_active_order(self):
        import threading
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        callback=Mock()
        def work():
            callback();entered.set();release.wait(2);finished.set()
        fulfillment.start_worker(7,'WORKER-TEST',work)
        self.assertTrue(entered.wait(1))
        fulfillment.start_worker(7,'WORKER-TEST',work)
        self.assertEqual(callback.call_count,1)
        release.set();self.assertTrue(finished.wait(1))

    def test_stock_zero_does_not_depend_on_description_or_price(self):
        for provider in ('dark','djekxa'):
            with patch.object(accounts.AccountSupplier,'product',return_value={'id':1358,'quantity':0}):
                self.assertEqual(imports.fetch_live_stock(1358,provider),0)
            with patch.object(accounts.AccountSupplier,'product',return_value={'id':1358,'quantity':None}):
                with self.assertRaises(imports.ImportFailure):imports.fetch_live_stock(1358,provider)

    def test_supplier_title_and_rub_price_updates_keep_delivery_format(self):
        for provider in ('dark','djekxa'):
            p=self.product(provider,3)
            old_title=p.title
            delivery.save_delivery_mapping(old_title,provider,str(p.product_id),True,imported=True,delivery_format='username|password')
            stock.save_mapping(7,1259,p.to_dict(),old_title,111,markup='50',rub_per_usd='100')
            fields=SimpleNamespace(lot_id=111,fields={'node_id':'1259'},title_en=old_title,title_ru=old_title,price=0.75)
            account=Mock(id=7)
            account.get_lot_fields.return_value=fields
            account.get_my_subcategory_lots.return_value=[]
            p.title='Renamed '+provider;p.price='100'
            with patch.object(stock,'fetch_product',return_value=p):stock.sync_metadata(account)
            self.assertEqual(fields.price,1.5)
            self.assertEqual(fields.title_en,p.title)
            row=next(row for row in stock.mappings() if row['provider']==provider)
            self.assertEqual(row['title'],p.title)
            api,auto=delivery.read_configs()
            self.assertEqual(api[p.title]['provider'],provider)
            self.assertEqual(auto[p.title]['deliveryFormat'],'username|password')
            with stock.database() as db:db.execute('DELETE FROM mappings')

    def test_slow_supplier_pre_handler_blocks_stale_stock_on_failure(self):
        import configparser
        import plugins.api_delivery as plugin
        from handlers import deliver_product_handler
        for failure in (False,True):
            cfg=configparser.ConfigParser()
            cfg['Lot']={'provider':'djekxa','product_id':'1358','enabled':'1'}
            auto=configparser.ConfigParser()
            auto['Lot']={'productsFileName':'test.txt','disable':'0','disableMultiDelivery':'0'}
            main=configparser.ConfigParser();main['FunPay']={'autoDelivery':'1'}
            c=SimpleNamespace(MAIN_CFG=main,AD_CFG=auto,blacklist=[],bl_delivery_enabled=False,
                multidelivery_enabled=True,account=SimpleNamespace(id=7),
                pre_delivery_handlers=[plugin.pre_delivery_handler],post_delivery_handlers=[])
            c.run_handlers=lambda callbacks,args:[fn(*args) for fn in callbacks]
            event=SimpleNamespace(config_section_name='Lot',config_section_obj=auto['Lot'],
                order=SimpleNamespace(id='TEST',buyer_username='demo',amount=1))
            with patch.object(plugin,'get_delivery_config',return_value=cfg),patch('plugins.providers.fulfillment.obtain',
                side_effect=accounts.SupplierError('synthetic') if failure else None,return_value=['user|password']) as obtain,\
                patch('Utils.cardinal_tools.add_products') as add,patch('handlers.deliver_goods') as send,\
                patch.object(fulfillment,'start_worker',side_effect=lambda account,order,callback:callback()):
                deliver_product_handler(c,event)
            if failure:
                send.assert_not_called();add.assert_not_called()
                self.assertTrue(event.api_delivery_blocked)
            else:
                send.assert_called_once()
                self.assertEqual(add.call_args.args[1],['user|password'])

    def test_settings_preserves_blank_keys_and_updates_provider_config(self):
        from fastapi.testclient import TestClient
        from admin.main import app
        from admin.auth import get_current_user
        import configparser
        keys=self.folder/'settings-keys.cfg'
        keys.write_text('[dark]\napi_key=existing-dark\n[djekxa]\napi_key=existing-djekxa\n[hstore]\napi_key=existing-hstore\n')
        cfg=self.folder/'settings-providers.json';cfg.write_text('{}')
        app.dependency_overrides[get_current_user]=lambda:'test-admin'
        try:
            with patch('admin.routes.settings.API_KEYS_CFG',str(keys)),patch('admin.routes.providers.PROVIDERS_CFG',str(cfg)):
                client=TestClient(app)
                page=client.get('/settings/').text
                self.assertIn('name="dark_key" value="existing-dark"',page)
                self.assertIn('name="djekxa_key" value="existing-djekxa"',page)
                response=client.post('/settings/save_api',data={'dark_key':'','djekxa_key':'updated-djekxa'},follow_redirects=False)
            self.assertEqual(response.status_code,303)
            saved=configparser.ConfigParser();saved.read(keys)
            self.assertEqual(saved['dark']['api_key'],'existing-dark')
            self.assertEqual(saved['hstore']['api_key'],'existing-hstore')
            self.assertEqual(saved['djekxa']['api_key'],'updated-djekxa')
            self.assertEqual(json.loads(cfg.read_text())['djekxa']['credentials'][0]['api_key'],'updated-djekxa')
        finally:
            app.dependency_overrides.clear()

    def test_djekxa_current_cdn_downloads_without_broadening_host_access(self):
        with patch.object(accounts,'get_credentials',return_value=[{'api_key':'synthetic'}]):
            client=accounts.AccountSupplier('djekxa')
        response=Mock(status_code=200,headers={'Content-Type':'text/plain'},content=b'demo|password\n')
        with patch.object(accounts.requests,'get',return_value=response) as get:
            self.assertEqual(client.download('https://cdn2gtdks.click/sold/demo.txt'),['demo|password'])
            self.assertFalse(get.call_args.kwargs['allow_redirects'])
        for url in ['https://cdn2gtdks.click.evil.test/private','https://cdn2gtdks.click@localhost/private']:
            with self.assertRaises(accounts.SupplierReview):client.download(url)
