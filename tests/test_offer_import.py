import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from admin.main import app
from admin.auth import get_current_user
from admin.routes import import_offers as routes
from admin.services.offer_import import (
    ImportFailure, Product, fetch_product, new_offer_fields, parse_product,
    product_url, selling_price, submit_offer, rejection_details, fetch_live_stock,
)

URL = 'https://hstora.com/en/product/1358-twitter-2006-2018-microsoft-email-auth-token-2fa'
HTML = '''<h1 class="product-main-title">Twitter &amp; Email</h1>
<div id="description"><div class="product-description">Account info\n\n2FA enabled<br>Keep this line</div></div>
<div id="productPurchasePanel"><span id="priceAmount">0.50$</span>
<span data-live-product-stock data-product-id="1358" data-stock-value="2126">2,126</span></div>'''
FORM = '''<form class="form-offer-editor">
<input type="hidden" name="csrf_token" value="fresh-token">
<input type="hidden" name="form_created_at" value="12345">
<input type="hidden" name="offer_id" value="0">
<input type="hidden" name="node_id" value="1259">
<input name="fields[summary][en]" value="">
<input type="checkbox" name="auto_delivery">
<div class="form-group"><input name="price"><span class="form-control-feedback">$</span></div>
</form>'''


class ProductTests(unittest.TestCase):
    def test_parse_current_page_structure(self):
        product = parse_product(HTML, URL)
        self.assertEqual((product.product_id, product.title, product.price, product.stock),
                         (1358, 'Twitter & Email', '0.50', 2126))
        self.assertEqual(product.description, 'Account info\n\n2FA enabled\nKeep this line')

    def test_initial_html_stock_without_javascript_attribute(self):
        product = parse_product(HTML.replace(' data-stock-value="2126"', ''), URL)
        self.assertEqual(product.stock, 2126)

    def test_sold_out_html_parses_as_zero(self):
        html = HTML.replace(' data-stock-value="2126"', '').replace('2,126', 'Out of Stock')
        self.assertEqual(parse_product(html, URL).stock,0)

    @patch('admin.services.offer_import.requests.get')
    def test_live_stock_zero_uses_storefront_endpoint(self, get):
        get.return_value.status_code=200
        get.return_value.json.return_value={'success':True,'stocks':{'1783':{'stock_quantity':0,'status':'active'}}}
        self.assertEqual(fetch_live_stock(1783),0)
        self.assertEqual(get.call_args.args[0],'https://hstora.com/api/product-stock')
        self.assertEqual(get.call_args.kwargs['params'],{'ids':'1783'})
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    @patch('admin.services.offer_import.requests.get')
    def test_bad_live_stock_never_becomes_zero(self,get):
        get.return_value.status_code=200
        payloads=[{'success':False}, {'success':True,'stocks':{}},
                  {'success':True,'stocks':{'1783':{'stock_quantity':None}}},
                  {'success':True,'stocks':{'1783':{'stock_quantity':False}}},
                  {'success':True,'stocks':{'1783':{'stock_quantity':-1}}}]
        for payload in payloads:
            get.return_value.json.return_value=payload
            with self.subTest(payload=payload),self.assertRaises(ImportFailure):fetch_live_stock(1783)
        get.return_value.status_code=503
        with self.assertRaises(ImportFailure):fetch_live_stock(1783)

    @patch('admin.services.offer_import.requests.get')
    def test_product_slug_redirect_stays_on_same_product(self,get):
        redirect=Mock(status_code=301,headers={'Location':'/en/product/1358-new-title'})
        page=Mock(status_code=200,text=HTML)
        get.side_effect=[redirect,page]
        self.assertEqual(fetch_product(URL).url,'https://hstora.com/en/product/1358-new-title')

    @patch('admin.services.offer_import.requests.get')
    def test_redirect_cannot_change_host_or_product(self,get):
        for location in ['https://evil.test/en/product/1358','/en/product/999-another-product']:
            get.return_value=Mock(status_code=301,headers={'Location':location})
            get.side_effect=None
            with self.subTest(location=location),self.assertRaises(ImportFailure):fetch_product(URL)

    def test_url_restrictions(self):
        for url in ['http://hstora.com/en/product/1358', 'https://hstora.com.evil/en/product/1358',
                    'https://hstora.com@localhost/en/product/1358', 'https://127.0.0.1/en/product/1358',
                    URL + '?redirect=http://localhost', URL + '#fragment']:
            with self.subTest(url=url), self.assertRaises(ImportFailure):
                product_url(url)

    def test_missing_fields_and_foreign_currency_fail_closed(self):
        for html in ['<h1>Login</h1>', HTML.replace('0.50$', '0.50€'),
                     HTML.replace('data-stock-value="2126"', 'data-stock-value="-1"'),
                     HTML.replace('data-product-id="1358"', 'data-product-id="999"')]:
            with self.subTest(html=html), self.assertRaises(ImportFailure):
                parse_product(html, URL)

    @patch('admin.services.offer_import.requests.get')
    def test_fetch_rejects_redirects(self, get):
        get.return_value.status_code = 302
        with self.assertRaises(ImportFailure):
            fetch_product(URL)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_markup_and_cent_rounding(self):
        self.assertEqual(selling_price('0.50', '30'), '0.65')
        self.assertEqual(selling_price('0.43', '25'), '0.54')
        self.assertEqual(selling_price('0.50', '0'), '0.50')
        for value in ['-1', 'NaN', 'Infinity', '100001', 'abc']:
            with self.subTest(value=value), self.assertRaises(ImportFailure):
                selling_price('0.50', value)

    def test_fresh_form_and_currency_validation(self):
        fields = new_offer_fields(FORM, 1259)
        self.assertEqual(fields['form_created_at'], '12345')
        self.assertEqual(fields['csrf_token'], 'fresh-token')
        self.assertNotIn('auto_delivery', fields)
        for html in [FORM.replace('$', '₽'), FORM.replace('1259', '1234'),
                     FORM.replace('value="0"', 'value="999"'),
                     FORM.replace('</form>', '<select name="fields[server]"><option>A</option></select></form>')]:
            with self.subTest(html=html), self.assertRaises(ImportFailure):
                new_offer_fields(html, 1259)

    def test_remote_rejection_details_are_sanitized_and_redacted(self):
        details = rejection_details({'error': '<b>Invalid request</b>', 'errors': [
            ['price', 'Too low'], ['csrf_token', 'Invalid secret-token'],
            ['field', '<script>alert(1)</script>golden_key=private-key']]},
            ['secret-token', 'private-key'])
        self.assertIn('price: Too low', details)
        self.assertNotIn('secret-token', details)
        self.assertNotIn('private-key', details)
        self.assertNotIn('<script>', details)
        self.assertNotIn('alert(1)', details)

    def test_remote_errors_and_ambiguous_response(self):
        account = Mock()
        for result in [{'error': 'Invalid price'}, {'errors': [['price', 'bad']]}, {}, []]:
            account.session.post.return_value.json.return_value = result
            with self.subTest(result=result), self.assertRaises(ImportFailure):
                submit_offer(account, {})
        account.session.post.return_value.json.return_value = {'done': True}
        self.assertEqual(submit_offer(account, {}), {'done': True})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        translation=patch('admin.services.offer_translation.translate_text',side_effect=lambda text,source,target:text)
        translation.start();self.addCleanup(translation.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.start_patch('admin.routes.import_offers.DB_PATH', Path(self.temp.name) / 'imports.sqlite3')
        self.start_patch('admin.services.offer_stock.DB_PATH', Path(self.temp.name) / 'stock.sqlite3')
        self.start_patch('admin.services.delivery_mapping.API_CFG', Path(self.temp.name) / 'api.cfg')
        self.start_patch('admin.services.delivery_mapping.AUTO_CFG', Path(self.temp.name) / 'auto.cfg')
        self.start_patch('admin.services.delivery_mapping.PRODUCTS', Path(self.temp.name) / 'products')
        self.start_patch('admin.routes.lots.API_DELIVERY_CFG', str(Path(self.temp.name) / 'api.cfg'))
        self.start_patch('admin.routes.lots.AUTO_DELIVERY_CFG', str(Path(self.temp.name) / 'auto.cfg'))
        self.restart = self.start_patch('admin.routes.import_offers.restart_cardinal', return_value=True)
        self.start_patch('admin.routes.import_offers.get_csrf_token', return_value='csrf')
        self.start_patch('admin.routes.import_offers.validate_csrf_token', side_effect=lambda t: t == 'csrf')
        self.product = Product(1358, URL, 'Twitter & Email', 'Full description\nLine two', '0.50', 2126)
        self.fetch = self.start_patch('admin.routes.import_offers.fetch_product', return_value=self.product)
        self.account = Mock(id=123, user_agent='test', golden_key='test-key', phpsessid='test-session', requests_timeout=30, proxy={})
        self.account.get_my_subcategory_lots.return_value = []
        self.account.method.return_value.text = FORM
        self.account.session.post.return_value.json.return_value = {'done': True}
        self.start_patch('admin.routes.import_offers.make_account', return_value=self.account)
        app.dependency_overrides[get_current_user] = lambda: 'test-admin'
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def start_patch(self, name, *args, **kwargs):
        p = patch(name, *args, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def preview(self):
        response = self.client.post('/imports/preview', data={'csrf_token': 'csrf', 'product_url': URL})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Twitter &amp; Email', response.text)
        with routes.database() as db:
            return db.execute('SELECT id FROM drafts ORDER BY created DESC LIMIT 1').fetchone()['id']

    def publish(self, draft_id, **changes):
        data = dict(csrf_token='csrf', draft=draft_id, title=self.product.title,
                    description=self.product.description, node=1259, quantity=10, markup='30')
        data.update(changes)
        return self.client.post('/imports/publish', data=data)

    def test_lots_entry_point_and_import_page(self):
        with patch('admin.routes.lots.get_csrf_token', return_value='csrf'):
            self.assertIn('href="/imports"', self.client.get('/lots/').text)
        self.assertIn('Import account offers', self.client.get('/imports/').text)

    def test_other_suppliers_publish_and_map_without_colliding_ids(self):
        from admin.services import offer_stock, delivery_mapping
        for provider, url in [('djekxa','https://djekxa.ru/product/1358'),('dark','https://dark.shopping/product/1358')]:
            self.product=Product(1358,url,provider+' offer','Description','50',100,'RUB',provider)
            self.fetch.return_value=self.product
            response=self.client.post('/imports/preview',data={'csrf_token':'csrf','product_url':url})
            self.assertIn('exchangeRate',response.text)
            with routes.database() as db:
                draft=db.execute('SELECT id FROM drafts ORDER BY created DESC LIMIT 1').fetchone()['id']
            response=self.publish(draft,rub_per_usd='100',markup='50')
            self.assertIn('Offer published successfully',response.text)
            self.assertEqual(self.account.session.post.call_args.kwargs['data']['price'],'0.75')
        self.assertEqual({m['provider'] for m in offer_stock.mappings(123)},{'dark','djekxa'})
        api,_=delivery_mapping.read_configs()
        self.assertEqual(api['dark offer']['provider'],'dark')
        self.assertEqual(api['djekxa offer']['provider'],'djekxa')
        response=self.client.post('/imports/markup',data={'csrf_token':'csrf','account_id':123,
            'node':1259,'product_id':1358,'provider':'dark','markup':'70','rub_per_usd':'90'},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        by_provider={m['provider']:m for m in offer_stock.mappings(123)}
        self.assertEqual(by_provider['dark']['rub_per_usd'],'90')
        self.assertEqual(by_provider['djekxa']['rub_per_usd'],'100')

    def test_bulk_minimum_is_not_listed_as_single_unit_offer(self):
        self.product.minimum_order=2
        draft=self.preview()
        response=self.publish(draft)
        self.assertIn('bulk minimum order',response.text)
        self.account.session.post.assert_not_called()

    def test_russian_import_uses_distinct_language_fields_and_delivery_aliases(self):
        from admin.services import delivery_mapping
        self.product=Product(32038,'https://djekxa.ru/product/32038','Русский заголовок','Русское описание','50',10,'RUB','djekxa')
        self.fetch.return_value=self.product
        listing={'title_en':'English title','title_ru':'Русский заголовок',
                 'description_en':'English description','description_ru':'Русское описание'}
        with patch.object(routes,'bilingual_listing',return_value=listing):
            page=self.client.post('/imports/preview',data={'csrf_token':'csrf','product_url':self.product.url})
        self.assertIn('English title',page.text)
        self.assertIn('Русский заголовок',page.text)
        with routes.database() as db:draft=db.execute('SELECT id FROM drafts').fetchone()['id']
        response=self.publish(draft,title=listing['title_en'],description=listing['description_en'],
            title_ru=listing['title_ru'],description_ru=listing['description_ru'],rub_per_usd='100',quantity=1)
        self.assertIn('Offer published successfully',response.text)
        fields=self.account.session.post.call_args.kwargs['data']
        for key,expected in [('fields[summary][en]','English title'),('fields[summary][ru]','Русский заголовок'),
                             ('fields[desc][en]','English description'),('fields[desc][ru]','Русское описание')]:
            self.assertEqual(fields[key],expected)
        api,auto=delivery_mapping.read_configs()
        self.assertEqual(api['English title']['product_id'],'32038')
        self.assertEqual(api['Русский заголовок']['provider'],'djekxa')
        self.assertEqual(auto['English title']['productsFileName'],auto['Русский заголовок']['productsFileName'])

    def test_translation_outage_still_allows_manual_review_without_publication(self):
        with patch.object(routes,'bilingual_listing',side_effect=ImportFailure('oi_translation_failed')):
            response=self.client.post('/imports/preview',data={'csrf_token':'csrf','product_url':URL})
        self.assertIn('Automatic translation is unavailable',response.text)
        self.assertIn('name="title_ru"',response.text)
        self.account.session.post.assert_not_called()

    def test_publish_requires_csrf(self):
        draft = self.preview()
        self.assertEqual(self.publish(draft, csrf_token='bad').status_code, 403)
        self.account.session.post.assert_not_called()

    def test_pages_require_authentication(self):
        app.dependency_overrides.clear()
        self.assertEqual(self.client.get('/imports/', follow_redirects=False).status_code, 303)

    def test_csrf_enforced_before_fetch(self):
        response = self.client.post('/imports/preview', data={'product_url': URL, 'csrf_token': 'bad'})
        self.assertEqual(response.status_code, 403)
        self.fetch.assert_not_called()

    def test_preview_does_not_publish(self):
        self.preview()
        self.account.method.assert_not_called()

    def test_publish_correct_payload_and_blocks_repeated_click(self):
        draft = self.preview()
        response = self.publish(draft)
        self.assertIn('Offer published successfully', response.text)
        call = self.account.session.post.call_args
        self.assertEqual(call.args[0], 'https://funpay.com/en/lots/offerSave')
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertEqual(self.account.session.mount.call_args.args[1].max_retries.total, 0)
        payload = call.kwargs['data']
        self.assertEqual(payload['price'], '0.65')
        self.assertEqual(payload['amount'], '10')
        self.assertEqual(payload['fields[summary][en]'], self.product.title)
        self.assertEqual(payload['fields[desc][en]'], self.product.description)
        self.assertEqual(payload['form_created_at'], '12345')
        self.assertEqual(payload['auto_delivery'], '')
        self.account.method.reset_mock()
        self.assertIn('already listed', self.publish(draft).text)
        self.account.session.post.assert_called_once()

    def test_remove_offer_cleans_maps_and_allows_reimport(self):
        from admin.services import offer_stock,delivery_mapping
        draft=self.preview()
        self.account.session.post.return_value.json.return_value={'done':True,'offer_id':79665384}
        self.publish(draft)
        delivery_mapping.alias_delivery_mapping(self.product.title,'Renamed',1358)
        _,auto=delivery_mapping.read_configs()
        inventory=delivery_mapping.PRODUCTS/auto[self.product.title]['productsFileName']
        inventory.write_text('preserved inventory')
        self.account.get_my_subcategory_lots.side_effect=[[SimpleNamespace(id=79665384)],[]]
        self.account.get_lot_fields.return_value=SimpleNamespace(lot_id=79665384,fields={'node_id':'1259'})
        page=self.client.get('/imports/remove?account_id=123&node=1259&product_id=1358')
        self.assertIn('I confirm removal',page.text)
        self.account.delete_lot.assert_not_called()
        response=self.client.post('/imports/remove',data={'csrf_token':'csrf','confirm':'true','account_id':123,'node':1259,'product_id':1358},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        self.account.delete_lot.assert_called_once_with(79665384)
        self.assertEqual(offer_stock.mappings(123),[])
        self.assertEqual(delivery_mapping.read_configs()[0].sections(),[])
        self.assertEqual(inventory.read_text(),'preserved inventory')
        with routes.database() as db:self.assertIsNone(db.execute('SELECT * FROM publications').fetchone())
        self.account.get_my_subcategory_lots.side_effect=None
        self.account.get_my_subcategory_lots.return_value=[]
        self.assertIn('Offer published successfully',self.publish(self.preview()).text)

    def test_remove_requires_csrf_confirmation_and_matching_account(self):
        from admin.services import offer_stock
        self.account.session.post.return_value.json.return_value={'done':True,'offer_id':79665384}
        self.publish(self.preview())
        data={'csrf_token':'bad','confirm':'true','account_id':123,'node':1259,'product_id':1358}
        self.assertEqual(self.client.post('/imports/remove',data=data).status_code,403)
        data.update(csrf_token='csrf',confirm='false')
        self.assertEqual(self.client.post('/imports/remove',data=data).status_code,400)
        data['confirm']='true';self.account.id=999
        self.assertIn('Removal did not complete',self.client.post('/imports/remove',data=data).text)
        self.account.delete_lot.assert_not_called()
        with offer_stock.database() as db:self.assertEqual(db.execute('SELECT state FROM mappings').fetchone()['state'],'removal_error')

    def test_remove_failure_preserves_delivery_and_can_retry(self):
        from admin.services import offer_stock,delivery_mapping
        self.account.session.post.return_value.json.return_value={'done':True,'offer_id':79665384}
        self.publish(self.preview())
        self.account.get_my_subcategory_lots.return_value=[SimpleNamespace(id=79665384)]
        self.account.get_lot_fields.return_value=SimpleNamespace(lot_id=79665384,fields={'node_id':'1259'})
        self.account.delete_lot.side_effect=TimeoutError()
        data={'csrf_token':'csrf','confirm':'true','account_id':123,'node':1259,'product_id':1358}
        self.assertIn('Removal did not complete',self.client.post('/imports/remove',data=data).text)
        self.assertIn(self.product.title,delivery_mapping.read_configs()[0])
        self.account.get_my_subcategory_lots.return_value=[]
        self.account.delete_lot.side_effect=None
        self.assertEqual(self.client.post('/imports/remove',data=data,follow_redirects=False).status_code,303)
        self.assertEqual(offer_stock.mappings(),[])

    def test_lots_format_can_be_saved_edited_and_cleared(self):
        from admin.services.delivery_mapping import save_delivery_mapping, read_configs
        save_delivery_mapping('Existing','hstore','1358',True)
        with patch('admin.routes.lots.validate_csrf_token',return_value=True),patch('admin.routes.lots.get_csrf_token',return_value='csrf'),patch('admin.routes.lots.restart_cardinal',return_value=True):
            for spec in ['username|password|refresh_token|auth_token','']:
                response=self.client.post('/lots/format',data={'lot_name':'Existing','csrf_token':'csrf','delivery_format':spec},follow_redirects=False)
                self.assertEqual(response.status_code,303)
                self.assertEqual(read_configs()[1]['Existing']['deliveryFormat'],spec)
            response=self.client.post('/lots/format',data={'lot_name':'Existing','csrf_token':'csrf','delivery_format':'unknown'})
            self.assertEqual(response.status_code,400)

    def test_successful_publication_enables_delivery_and_appears_in_lots(self):
        self.publish(self.preview())
        from admin.services.delivery_mapping import read_configs, PRODUCTS
        api, auto = read_configs()
        self.assertEqual(api[self.product.title]['provider'],'hstore')
        self.assertEqual(api[self.product.title]['product_id'],'1358')
        self.assertEqual(api[self.product.title]['enabled'],'1')
        self.assertIn('$product',auto[self.product.title]['response'])
        self.assertEqual(auto[self.product.title]['disable'],'0')
        self.assertEqual(auto[self.product.title]['disableAutoDisable'],'1')
        self.assertEqual(auto[self.product.title]['disableAutoRestore'],'1')
        self.assertTrue((PRODUCTS/auto[self.product.title]['productsFileName']).exists())
        self.restart.assert_called_once()
        with patch('admin.routes.lots.get_csrf_token',return_value='csrf'):
            self.assertIn('Twitter &amp; Email',self.client.get('/lots/').text)

    def test_delivery_restart_failure_keeps_publish_success_and_mapping(self):
        self.restart.return_value=False
        draft=self.preview()
        response=self.publish(draft)
        self.assertIn('Offer published successfully',response.text)
        self.assertIn('Cardinal restart failed',response.text)
        self.assertIn('already listed',self.publish(draft).text)
        self.account.session.post.assert_called_once()

    def test_delivery_write_failure_does_not_invite_duplicate_publish(self):
        draft=self.preview()
        with patch('admin.routes.import_offers.save_delivery_mapping',side_effect=OSError()):
            response=self.publish(draft)
        self.assertIn('Offer published successfully',response.text)
        self.assertIn('delivery mapping could not be saved',response.text)
        self.assertIn('already listed',self.publish(draft).text)
        self.restart.assert_not_called()

    def test_conflicting_delivery_mapping_blocks_publish(self):
        from admin.services.delivery_mapping import save_delivery_mapping
        save_delivery_mapping(self.product.title,'hstore','999',True)
        response=self.publish(self.preview())
        self.assertIn('incompatible delivery mapping',response.text)
        self.account.session.post.assert_not_called()
        self.restart.assert_not_called()

    def test_successful_publication_persists_supplier_mapping(self):
        self.account.session.post.return_value.json.return_value = {'done': True, 'offer_id': 79665384}
        self.publish(self.preview())
        from admin.services.offer_stock import mappings
        rows = mappings(123)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['offer_id'],79665384)
        self.assertEqual(rows[0]['source_url'],URL)
        self.assertEqual(rows[0]['title'], self.product.title)

    def test_existing_offer_can_be_mapped_without_publishing(self):
        self.account.get_lot_fields.return_value = SimpleNamespace(
            lot_id=79665384, fields={'node_id':'1259'},title_en='Twitter',title_ru='')
        response = self.client.post('/imports/track', data={'csrf_token':'csrf','product_url':URL,
            'offer_url':'https://funpay.com/en/lots/offerEdit?node=1259&offer=79665384'})
        self.assertIn('Stock mapping saved',response.text)
        from admin.services.offer_stock import mappings
        self.assertEqual(mappings(123)[0]['offer_id'],79665384)
        self.account.session.post.assert_not_called()

    def test_mapping_rejects_csrf_and_wrong_category(self):
        data = {'csrf_token':'bad','product_url':URL,
                'offer_url':'https://funpay.com/en/lots/offerEdit?node=1259&offer=79665384'}
        self.assertEqual(self.client.post('/imports/track',data=data).status_code,403)
        data['csrf_token']='csrf'
        self.account.get_lot_fields.return_value = SimpleNamespace(lot_id=79665384,fields={'node_id':'777'})
        response=self.client.post('/imports/track',data=data)
        self.assertIn('Could not link',response.text)
        from admin.services.offer_stock import mappings
        self.assertEqual(mappings(123),[])

    def test_untrusted_draft_stock_and_markup_cannot_publish(self):
        draft = self.preview()
        for changes in [dict(draft='tampered'), dict(quantity=3000), dict(markup='-1'), dict(title='x' * 101)]:
            self.publish(draft, **changes)
        self.account.method.assert_not_called()
        self.account.session.post.assert_not_called()

    def test_description_limit_accepts_817_and_rejects_818(self):
        draft = self.preview()
        response = self.publish(draft, description='x' * 818)
        self.assertIn('1–817', response.text)
        self.account.session.post.assert_not_called()
        response = self.publish(draft, description='x' * 817)
        self.assertIn('Offer published successfully', response.text)
        payload = self.account.session.post.call_args.kwargs['data']
        self.assertEqual(len(payload['fields[desc][en]']), 817)
        self.assertEqual(len(payload['fields[desc][ru]']), 817)

    def test_browser_crlf_submission_at_description_limit(self):
        # Textareas display LF but browsers serialize newlines as CRLF.
        description = ('Account information\n\n' * 30 + 'x' * 817)[:817]
        self.assertEqual(len(description), 817)
        submitted = description.replace('\n', '\r\n')
        self.assertGreater(len(submitted), 817)
        response = self.publish(self.preview(), description=submitted)
        self.assertIn('Offer published successfully', response.text)
        payload = self.account.session.post.call_args.kwargs['data']
        self.assertEqual(payload['fields[desc][en]'], description)
        self.assertNotIn('\r', payload['fields[desc][en]'])

    def test_imported_description_is_shortened_in_preview(self):
        self.product.description = 'x' * 900
        self.preview()
        response = self.client.post('/imports/preview', data={'csrf_token': 'csrf', 'product_url': URL})
        self.assertIn('maxlength="817"', response.text)
        self.assertIn('x' * 817 + '</textarea>', response.text)
        self.assertNotIn('x' * 818, response.text)
        self.assertIn('shortened to 817', response.text)

    def test_price_change_requires_new_preview(self):
        draft = self.preview()
        self.fetch.return_value = Product(1358, URL, 'Title', 'Description', '0.60', 2126)
        self.assertIn('price or available stock changed', self.publish(draft).text)
        self.account.method.assert_not_called()

    def test_timeout_claim_survives_new_preview(self):
        draft = self.preview()
        self.account.session.post.side_effect = TimeoutError()
        self.assertIn('may have received', self.publish(draft).text)
        self.account.session.post.side_effect = None
        self.account.method.return_value.text = FORM
        self.account.method.reset_mock()
        self.assertIn('already listed', self.publish(self.preview()).text)
        self.account.session.post.assert_called_once()

    def test_explicit_republish_after_deletion_requires_fresh_preview(self):
        original = self.preview()
        self.assertIn('Offer published successfully', self.publish(original).text)
        self.assertIn('already listed', self.publish(original, republish_deleted='true').text)
        fresh = self.preview()
        self.assertIn('Offer published successfully', self.publish(fresh, republish_deleted='true').text)
        self.assertIn('already listed', self.publish(fresh, republish_deleted='true').text)
        self.assertEqual(self.account.session.post.call_count, 2)

    def test_republish_cannot_override_uncertain_submission(self):
        original = self.preview()
        self.account.session.post.side_effect = TimeoutError()
        self.publish(original)
        self.account.session.post.side_effect = None
        response = self.publish(self.preview(), republish_deleted='true')
        self.assertIn('already listed', response.text)
        self.account.session.post.assert_called_once()

    def test_republish_cannot_override_live_matching_offer(self):
        self.publish(self.preview())
        self.account.get_my_subcategory_lots.return_value = [SimpleNamespace(description=self.product.title)]
        response = self.publish(self.preview(), republish_deleted='true')
        self.assertIn('already listed', response.text)
        self.account.session.post.assert_called_once()

    def test_existing_funpay_title_blocks_publish(self):
        self.account.get_my_subcategory_lots.return_value = [SimpleNamespace(description=self.product.title)]
        self.assertIn('already listed', self.publish(self.preview()).text)
        self.account.session.post.assert_not_called()

    def test_explicit_rejection_allows_retry(self):
        draft = self.preview()
        self.account.session.post.return_value.json.return_value = {'errors': [['price', 'too low']]}
        response = self.publish(draft)
        self.assertIn('FunPay rejected', response.text)
        self.assertIn('price: too low', response.text)
        self.account.session.post.return_value.json.return_value = {'done': True}
        self.assertIn('Offer published successfully', self.publish(draft).text)


if __name__ == '__main__':
    unittest.main()
