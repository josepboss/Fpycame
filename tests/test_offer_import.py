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
    product_url, selling_price, submit_offer,
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
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.start_patch('admin.routes.import_offers.DB_PATH', Path(self.temp.name) / 'imports.sqlite3')
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
            return db.execute('SELECT id FROM drafts').fetchone()['id']

    def publish(self, draft_id, **changes):
        data = dict(csrf_token='csrf', draft=draft_id, title=self.product.title,
                    description=self.product.description, node=1259, quantity=10, markup='30')
        data.update(changes)
        return self.client.post('/imports/publish', data=data)

    def test_lots_entry_point_and_import_page(self):
        with patch('admin.routes.lots.get_csrf_token', return_value='csrf'):
            self.assertIn('href="/imports"', self.client.get('/lots/').text)
        self.assertIn('Import from HStore', self.client.get('/imports/').text)

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

    def test_untrusted_draft_stock_and_markup_cannot_publish(self):
        draft = self.preview()
        for changes in [dict(draft='tampered'), dict(quantity=3000), dict(markup='-1'), dict(title='x' * 101)]:
            self.publish(draft, **changes)
        self.account.method.assert_not_called()
        self.account.session.post.assert_not_called()

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

    def test_existing_funpay_title_blocks_publish(self):
        self.account.get_my_subcategory_lots.return_value = [SimpleNamespace(description=self.product.title)]
        self.assertIn('already listed', self.publish(self.preview()).text)
        self.account.session.post.assert_not_called()

    def test_explicit_rejection_allows_retry(self):
        draft = self.preview()
        self.account.session.post.return_value.json.return_value = {'errors': [['price', 'too low']]}
        self.assertIn('FunPay rejected', self.publish(draft).text)
        self.account.session.post.return_value.json.return_value = {'done': True}
        self.assertIn('Offer published successfully', self.publish(draft).text)


if __name__ == '__main__':
    unittest.main()
